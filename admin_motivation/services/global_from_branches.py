"""Rebuild global K0–K4 as plan-weighted local rates, with explicit coverage."""
import hashlib,json
from collections import defaultdict
from decimal import Decimal,ROUND_HALF_UP
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import models,transaction
from base.access import can_access
from ..models import (GlobalCoeff,GlobalDirectorCoeff,SubdivisionDirectorCoeff,CuratorCoefficient,
    DirectorEmployee,CuratorEmployee,Goods,SalesPlanScenario,CoefficientGroupCopyLog,touch_version)
from .subdivision_scope import with_exclusions,included,policy_signature
from .subdivision_aliases import mapping,canonical
from .comparison import coefficient_rows,coefficient_input
from .directors import lock_editable

GROUPS={'policies':'Политики','sales':'Продажи','directors':'Директора','curators':'Кураторы'}
ZERO=Decimal(0)


def segment_index(label):
    text=label.upper().replace('К','K').replace('О','O')
    return int(text[1]) if len(text)==2 and text[0] in 'KO' and text[1] in '01234' else None


@with_exclusions
def preview(version,scenario,groups):
    groups=list(dict.fromkeys(groups))
    if not groups or set(groups)-GROUPS.keys():raise ValidationError('Выберите виды коэффициентов.')
    if scenario.is_deleted:raise ValidationError('План удалён из рабочих списков.')
    goods={};by_class={}
    for good in Goods.objects.order_by('pk'):
        key=(good.planning_group_sales,good.group,good.brand)
        if key in by_class:raise ValidationError('В справочнике есть неоднозначная товарная классификация. Устраните дубли перед сборкой.')
        goods[good.pk]=good;by_class[key]=good.pk
    names=mapping();core={}
    for ver,sub,pg,group,brand,kind,segment,value in coefficient_rows([version.pk],subdivision=True):
        index=segment_index(segment)
        if index is not None:core[(sub,pg,group,brand,kind,index)]=coefficient_input(value)
    staff={};staff_missing={}
    for key,model,employee_model in (('directors',SubdivisionDirectorCoeff,DirectorEmployee),('curators',CuratorCoefficient,CuratorEmployee)):
        if key not in groups:continue
        # Only active employees still bound to the physical branch participate.
        through=employee_model.subdivisions.through
        employee_field='directoremployee_id' if key=='directors' else 'curatoremployee_id'
        active=set(employee_model.objects.filter(is_archived=False).values_list('pk',flat=True))
        bindings={(employee,branch) for employee,branch in through.objects.values_list(employee_field,'subdivision_id') if employee in active}
        raw=[]
        for row in model.objects.filter(version=version).select_related('subdivision').order_by('pk'):
            if (row.employee_id,row.subdivision_id) not in bindings:continue
            raw.append((row.subdivision.subdivision_key,row.employee_id,row.goods_id,*[getattr(row,f'k{i}') for i in range(5)]))
        if key=='directors':
            from .subdivision_aliases import coefficient_rows as merge
            raw=list(merge(raw,0,range(3)))
        rates=defaultdict(lambda:[ZERO]*5)
        for sub,employee,good,*values in raw:
            for index,value in enumerate(values):rates[(sub,good)][index]+=coefficient_input(value)
        staff[key]=rates
        staff_missing[key]=(set((sub,person) for sub,person,good,*values in raw),set((sub,person,good) for sub,person,good,*values in raw))
    targets={}
    for cell in GlobalCoeff.objects.filter(version=version).select_related('type_coeff','segment'):
        key=next((key for key in ('policies','sales','curators') if GROUPS[key]==cell.type_coeff.type_coeff_name),None)
        index=segment_index(cell.segment.segment_name)
        if key in groups and index is not None:targets[(key,cell.goods_id,index)]=cell
    if 'directors' in groups:
        for row in GlobalDirectorCoeff.objects.filter(version=version):
            for index in range(5):targets[('directors',row.goods_id,index)]=row
    weights=defaultdict(Decimal);unknown=set();negative=set();line_count=0
    from .motivation_exclusions import amounts_for
    for sub,pg,group,brand,segments,sources in scenario.lines.exclude(subdivision='').order_by().values_list('subdivision','planning_group_sales','group','brand','segment_amounts','source_subdivisions').iterator(chunk_size=2000):
        if not included(sub,names):continue
        good=by_class.get((pg,group,brand))
        if good is None:
            if any(Decimal(str(segments.get(f'O{i}',0))) for i in range(5)):unknown.add((pg,group,brand))
            continue
        line_count+=1
        for key in groups:
            # Curators retain physical branches; a combined snapshot cannot invent their split.
            if key=='curators' and sources and len(sources)>1:
                raise ValidationError('Для сборки кураторов нужен план с отдельными физическими подразделениями, без объединённой строки нескольких источников.')
            branch=sub if key=='curators' else canonical(sub,names)
            for index,value in enumerate(amounts_for(GROUPS[key],segments,pg,sources)):
                if not value.is_finite() or value<0:negative.add((sub,pg,group,brand))
                weights[(key,good,index,branch)]+=value
    totals=defaultdict(lambda:[ZERO,ZERO,[]]);missing=set()
    for (key,good,index,branch),weight in sorted(weights.items()):
        if not weight:continue
        item=goods[good]
        if key in ('policies','sales'):
            rate=core.get((branch,item.planning_group_sales,item.group,item.brand,GROUPS[key],index))
            if rate is None:missing.add((GROUPS[key],branch,str(item),f'K{index}'));continue
        else:
            # No assigned recipient/rate means no local fund, matching the local budget.
            recipients,exact=staff_missing[key]
            if any((branch,person,good) not in exact for sub,person in recipients if sub==branch):
                missing.add((GROUPS[key],branch,str(item),f'K{index} · не все ставки сотрудников'));continue
            rate=staff[key].get((branch,good),[ZERO]*5)[index]
        target=totals[(key,good,index)]
        target[0]+=weight*rate;target[1]+=weight
        target[2].append({'branch':branch,'weight':str(weight),'rate':str(rate),'rate_percent':rate*100})
    rows=[];missing_targets=[]
    keys=set(targets)|set(totals)
    for key,good,index in sorted(keys):
        numerator,denominator,parts=totals[(key,good,index)]
        target=targets.get((key,good,index));before=Decimal(str(getattr(target,f'k{index}') if key=='directors' and target else target.motivation_coeff if target else 0)) if target else None
        after=(numerator/denominator).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP) if denominator else None
        if after is not None and not -1<=after<=1:raise ValidationError('Суммарная ставка получателей превышает диапазон −100…100%. Сборка отменена.')
        if denominator and target is None:missing_targets.append(f'{GROUPS[key]} · {goods[good]} · K{index}')
        rows.append({'kind':GROUPS[key],'group':key,'goods':good,'label':str(goods[good]),'segment':index,'target':target.pk if target else None,'before':str(before) if before is not None else None,'after':str(after) if after is not None else None,'before_percent':before*100 if before is not None else None,'after_percent':after*100 if after is not None else None,'weight':str(denominator),'parts':parts})
    ready=any(value[1]>0 for value in totals.values()) and not (missing or unknown or negative or missing_targets)
    digest=hashlib.sha256(json.dumps({'rows':rows,'policy':policy_signature(),'version':version.pk,'revision':version.revision,'scenario':scenario.pk,'groups':groups},sort_keys=True,default=str).encode()).hexdigest()
    return {'rows':rows,'missing':sorted(missing),'unknown':sorted(unknown),'negative':sorted(negative),'missing_targets':missing_targets,'ready':ready,'digest':digest,'line_count':line_count,'changed':sum(r['after'] is not None and (r['before'] is None or Decimal(r['after'])!=Decimal(r['before'])) for r in rows)}


@transaction.atomic
def apply(user,version_id,scenario_id,groups,revision,digest):
    if not can_access(user,'motivation_editor'):raise PermissionDenied
    version=lock_editable(version_id)
    if str(version.revision)!=str(revision):raise ValidationError('Версия изменилась. Повторите предварительный просмотр.')
    scenario=SalesPlanScenario.objects.select_for_update().get(pk=scenario_id)
    result=preview(version,scenario,groups)
    if not result['ready'] or result['digest']!=digest:raise ValidationError('План, ставки или справочники изменились. Повторите предварительный просмотр.')
    changes=[]
    for row in result['rows']:
        if row['after'] is None:continue
        if row['group']=='directors':
            target=GlobalDirectorCoeff.objects.get(pk=row['target']);setattr(target,f"k{row['segment']}",Decimal(row['after']))
        else:
            target=GlobalCoeff.objects.get(pk=row['target']);target.motivation_coeff=float(row['after'])
        target.full_clean();models.Model.save(target)
        changes.append({**{key:row[key] for key in ('goods','kind','segment','before','after','weight','parts')},'plan':scenario.pk,'plan_title':scenario.title})
    CoefficientGroupCopyLog.objects.create(version=version,actor=user.get_username(),source_group='subdivisions',target_group='global',changes=json.loads(json.dumps(changes,default=str)))
    touch_version(version.pk)
    return len(changes)
