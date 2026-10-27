"""Transactional before/after journal for import, copy and explicit creation operations."""
from collections import defaultdict
from functools import wraps
from django.db import transaction
from ..models import (GlobalCoeffVersion,GlobalCoeff,SubdivisionCoeff,GlobalDirectorCoeff,SubdivisionDirectorCoeff,SubdivisionManagerCoeff,CoefficientEditLog,Goods,TypeCoeff,SegmentCoeff,Subdivision,DirectorEmployee)

MODELS=(GlobalCoeff,SubdivisionCoeff,GlobalDirectorCoeff,SubdivisionDirectorCoeff,SubdivisionManagerCoeff)
ACTIONS={'file_load','apply_sub','copy_version','copy_from_global','copy_to_director','fill_coeff','add_product'}
LABELS={'balancing':'Балансировка премии','file_load':'Импорт файла','apply_sub':'Копирование в подразделения','copy_version':'Копирование версии','copy_from_global':'Копирование глобальных ставок','copy_to_director':'Копирование директору','fill_coeff':'Заполнение матрицы','add_product':'Добавление товара','coefficient_group_copy':'Копирование групп','director_coeff_import':'Импорт директоров','coverage':'Создание отсутствующих ставок'}


def snapshot(version):
    result={}
    for model in MODELS:
        for row in model.objects.filter(version=version).values().iterator(chunk_size=2000):
            identity=(model.__name__,row.get('subdivision_id'),row.get('employee_id'),row.get('goods_id'),row.get('type_coeff_id'),row.get('segment_id'),row.get('kind_id'))
            fields={key:str(row[key]) if row[key] is not None else None for key in ('motivation_coeff','manager_coeff','variation_calculate_id','coeff','k0','k1','k2','k3','k4') if key in row}
            result[identity]={'fields':fields,'pk':row['id']}
    return result


def record(actor,version,before,operation,revision_before):
    after=snapshot(version);groups=defaultdict(list)
    goods={good.pk:' / '.join([good.planning_group_sales or '—',good.group or '—',good.brand or '—']) for good in Goods.objects.filter(pk__in={key[3] for key in before.keys()|after.keys() if key[3]})}
    kinds=dict(TypeCoeff.objects.values_list('pk','type_coeff_name'));segments=dict(SegmentCoeff.objects.values_list('pk','segment_name'))
    for identity in before.keys()|after.keys():
        old,new=before.get(identity),after.get(identity)
        if old and new and old['fields']==new['fields']:continue
        model,sub,employee,good,kind,segment,manager=identity
        common={'model':model,'pk':(new or old)['pk'],'goods_id':good,'goods_label':goods.get(good,'Поправки руководителя'),'kind':kinds.get(kind,'Директор' if 'Director' in model else 'Поправки'),'segment':segments.get(segment,''),'operation':LABELS.get(operation,operation)}
        if not new:groups[sub,employee].append({**common,'field':'delete','before':old['fields'],'after':None});continue
        for field,value in new['fields'].items():
            previous=old['fields'].get(field) if old else None
            if old and previous==value:continue
            groups[sub,employee].append({**common,'field':field,'segment':common['segment'] or field.upper(),'before':previous,'after':value})
    subs={row.pk:str(row) for row in Subdivision.objects.all()};employees=dict(DirectorEmployee.objects.values_list('pk','full_name'))
    revision=GlobalCoeffVersion.objects.get(pk=version.pk).revision
    for (sub,employee),changes in groups.items():
        scope=(subs.get(sub,'Глобальные коэффициенты')+(' · '+employees.get(employee,'Директор') if employee else '')+' · '+LABELS.get(operation,operation))[:512]
        CoefficientEditLog.objects.create(version=version,subdivision_id=sub,employee_id=employee,actor=actor,actor_label=(actor.get_full_name() or actor.get_username())[:255],scope_label=scope,revision_before=revision_before,revision_after=revision,changed_count=len(changes),changes=changes)


def audit_request(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        action=request.POST.get('action_button') or view.__name__
        if request.method!='POST' or action not in ACTIONS|{'coefficient_group_copy','director_coeff_import'} or not str(request.POST.get('version','')).isdigit():return view(request,*args,**kwargs)
        from base.access import can_access
        if not can_access(request.user,'motivation_editor'):return view(request,*args,**kwargs)
        with transaction.atomic():
            versions=list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
            version=next((version for version in versions if version.pk==int(request.POST['version'])),None)
            if not version or not version.is_editable:return view(request,*args,**kwargs)
            before=snapshot(version);revision=version.revision
            response=view(request,*args,**kwargs)
            record(request.user,version,before,action,revision)
            return response
    return wrapped
