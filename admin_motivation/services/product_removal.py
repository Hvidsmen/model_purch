"""Explicit preview and draft-only removal of product rates across scopes."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from base.access import can_access
from ..models import Goods, GlobalCoeff, GlobalDirectorCoeff, CoefficientEditLog
from .directors import lock_editable
from .planning_group_rename import MODELS, LABELS


def available(version, orphan_only=False):
    ids=set()
    for model in MODELS:ids.update(model.objects.filter(version=version).values_list('goods_id',flat=True))
    if orphan_only:
        for model in (GlobalCoeff,GlobalDirectorCoeff):ids.difference_update(model.objects.filter(version=version).values_list('goods_id',flat=True))
    return Goods.objects.filter(pk__in=ids).order_by('planning_group_sales','group','brand','pk')


def preview(version, goods_ids, include_local):
    ids=sorted(set(goods_ids))
    if not ids or set(available(version).filter(pk__in=ids).values_list('pk',flat=True))!=set(ids):
        raise ValidationError('Выберите товары со ставками в текущем черновике.')
    counts=[]; kinds={}; scopes=[]; total=0
    for model,label in zip(MODELS,LABELS):
        if not include_local and model not in (GlobalCoeff,GlobalDirectorCoeff):continue
        qs=model.objects.filter(version=version,goods_id__in=ids)
        count=qs.count();total+=count
        counts.append(dict(label=label,count=count))
        if model in MODELS[:2]:
            for row in qs.values('type_coeff__type_coeff_name').annotate(total=Count('pk')):
                name=row['type_coeff__type_coeff_name'];kinds[name]=kinds.get(name,0)+row['total']
        else:
            name='Кураторы' if model is MODELS[-1] else 'Директора'
            kinds[name]=kinds.get(name,0)+count
        if model not in (GlobalCoeff,GlobalDirectorCoeff):
            for row in qs.values('subdivision__subdivision_name').annotate(total=Count('pk')):
                scopes.append(dict(label=label,subdivision=row['subdivision__subdivision_name'],count=row['total']))
    if not total:raise ValidationError('Нет ставок для удаления. Для оставшихся местных ставок отметьте удаление во всех подразделениях.')
    return dict(ids=ids,goods=list(Goods.objects.filter(pk__in=ids)),counts=counts,kinds=sorted(kinds.items()),scopes=scopes,total=total,include_local=include_local)


@transaction.atomic
def apply(actor,version_id,revision,goods_ids,include_local):
    if not can_access(actor,'motivation_editor'):raise PermissionDenied
    version=lock_editable(version_id)
    if str(version.revision)!=str(revision):raise ValidationError('Версия изменилась. Повторите предварительный просмотр.')
    plan=preview(version,goods_ids,include_local)
    changes=[];before=version.revision
    for model in MODELS:
        if not include_local and model not in (GlobalCoeff,GlobalDirectorCoeff):continue
        qs=model.objects.filter(version=version,goods_id__in=plan['ids'])
        for row in qs.values():
            changes.append(dict(model=model.__name__,pk=row['id'],goods_id=row['goods_id'],field='delete',
                before={k:str(v) if v is not None else None for k,v in row.items()},after=None))
        if qs.exists():qs.delete()
    version.refresh_from_db()
    CoefficientEditLog.objects.create(version=version,actor=actor,actor_label=actor.get_full_name() or actor.username,
        scope_label='Удаление товаров: глобально'+(' и во всех подразделениях' if include_local else ''),
        revision_before=before,revision_after=version.revision,changed_count=plan['total'],changes=changes)
    return plan
