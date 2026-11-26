"""Move draft rates to a new product key without renaming shared Goods."""
from copy import deepcopy
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from base.access import can_access
from ..models import (Goods, GlobalCoeff, SubdivisionCoeff, GlobalDirectorCoeff,
                      SubdivisionDirectorCoeff, CuratorCoefficient, CoefficientEditLog)
from .directors import lock_editable
from .planning_group_rules import validate

MODELS=(GlobalCoeff, SubdivisionCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff, CuratorCoefficient)
LABELS=('Глобальные ставки политиков, продаж и кураторов', 'Ставки подразделений',
        'Глобальные ставки директоров', 'Директора подразделений', 'Кураторы подразделений')


def preview(version, source, target):
    target=target.strip()
    if not source or not target or source==target or len(target)>255:
        raise ValidationError('Выберите исходную группу и укажите другое название до 255 символов.')
    counts=[]; goods_ids=set(); subdivisions=set(); kinds={}
    for model,label in zip(MODELS,LABELS):
        qs=model.objects.filter(version=version,goods__planning_group_sales=source)
        counts.append({'label':label,'count':qs.count()})
        if model in (GlobalCoeff,SubdivisionCoeff):
            from django.db.models import Count
            for row in qs.values('type_coeff__type_coeff_name').annotate(total=Count('pk')):
                name=row['type_coeff__type_coeff_name'];kinds[name]=kinds.get(name,0)+row['total']
        else:
            name='Кураторы' if model is CuratorCoefficient else 'Директора'
            kinds[name]=kinds.get(name,0)+qs.count()
        goods_ids.update(qs.values_list('goods_id',flat=True))
        if any(f.name=='subdivision' for f in model._meta.fields):
            subdivisions.update(qs.values_list('subdivision__subdivision_name',flat=True))
    goods=list(Goods.objects.filter(pk__in=goods_ids).order_by('pk'))
    conflicts=[]
    for good in goods:
        matches=list(Goods.objects.filter(planning_group_sales=target,group=good.group,brand=good.brand))
        key=target+good.group+good.brand
        if len(key)>255:conflicts.append(f'{good.group} / {good.brand}: ключ длиннее 255 символов.')
        elif len(matches)>1:conflicts.append(f'{good.group} / {good.brand}: несколько товаров с новым названием.')
        elif not matches and Goods.objects.filter(goods_key=key).exists():
            conflicts.append(f'{good.group} / {good.brand}: новый ключ занят другим товаром.')
        elif matches and any(m.objects.filter(version=version,goods=matches[0]).exists() for m in MODELS):
            conflicts.append(f'{good.group} / {good.brand}: в черновике уже есть ставки под новым названием.')
    renamed=deepcopy(version.planning_rules)
    rule_count=0
    for row in renamed:
        if row['planning']==source:row['planning']=target;rule_count+=1
    try:validate(renamed)
    except ValidationError:conflicts.append('Под новым названием уже есть пересекающиеся правила группы.')
    if not goods and not rule_count:raise ValidationError('В выбранной версии нет ставок или правил этой группы.')
    return dict(source=source,target=target,counts=counts,goods=goods,subdivisions=sorted(subdivisions),
                kind_counts=sorted(kinds.items()),rule_count=rule_count,rules=renamed,conflicts=conflicts,total=sum(r['count'] for r in counts))


@transaction.atomic
def apply(actor, version_id, revision, source, target):
    if not can_access(actor,'motivation_editor'):raise PermissionDenied
    version=lock_editable(version_id)
    if str(version.revision)!=str(revision):raise ValidationError('Версия изменилась. Повторите предварительный просмотр.')
    plan=preview(version,source,target)
    if plan['conflicts']:raise ValidationError('Переименование остановлено: устраните конфликты.')
    before=version.revision; changes=[]
    for good in plan['goods']:
        replacement=Goods.objects.filter(planning_group_sales=plan['target'],group=good.group,brand=good.brand).first()
        if replacement is None:
            replacement=Goods.objects.create(planning_group_sales=plan['target'],group=good.group,brand=good.brand,
                                             goods_key=plan['target']+good.group+good.brand)
        for model in MODELS:
            qs=model.objects.filter(version=version,goods=good)
            ids=list(qs.values_list('pk',flat=True))
            if ids:
                qs.update(goods=replacement)
                changes.append(dict(model=model.__name__,pks=ids,goods_id=replacement.pk,kind='Переименование группы',
                                    field='planning_group_sales',before=source,after=plan['target'],
                                    source_goods_id=good.pk,target_goods_id=replacement.pk))
    if plan['rule_count']:
        changes.append(dict(field='planning_group_sales',kind='Правила группы',goods_label=source,
                            before=source,after=plan['target']))
    version.refresh_from_db()
    version.planning_rules=plan['rules']
    version.revision+=1
    version.save(update_fields=['planning_rules','revision'])
    CoefficientEditLog.objects.create(version=version,actor=actor,actor_label=actor.get_full_name() or actor.username,
        scope_label=f'Переименование группы: {source} → {plan["target"]}',revision_before=before,
        revision_after=version.revision,changed_count=plan['total']+plan['rule_count'],changes=changes)
    return plan
