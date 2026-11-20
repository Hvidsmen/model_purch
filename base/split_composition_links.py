"""Edit shared directory links and the current scenario snapshot atomically."""
import json
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import QueryDict
from .sales_planning_access import groups_for
from .sales_planning_groups import GroupSettingsForm
from .split_workflow import changed_state,persist


def directory_group(scenario,user,gid,*,lock=False):
    entry=scenario.state['groups'].get(gid)
    if not entry:raise ValidationError('Выберите группу сценария.')
    queryset=groups_for(user)
    if lock:queryset=queryset.select_for_update()
    group=queryset.filter(source_key=entry['ref']['source_key']).first()
    if not group:raise ValidationError('Группа недоступна или отсутствует в справочнике.')
    return group


def catalog(scenario,user,gid):
    group=directory_group(scenario,user,gid)
    form=GroupSettingsForm(instance=group,user=user)
    return {'name':group.name,'sections':[{'key':key,'label':form.fields[key].label,
        'choices':[{'id':str(g.pk),'name':g.name,'active':g.is_active} for g in form.fields[key].queryset.order_by('name')],
        'selected':list(map(str,getattr(group,key).filter(pk__in=form.fields[key].queryset).values_list('pk',flat=True)))}
        for key in ('related_groups','replacement_groups')]}


@transaction.atomic
def save(scenario,user,post):
    try:patches=json.loads(post.get('composition_links','{}'))
    except (ValueError,TypeError):raise ValidationError('Некорректный список связей.')
    if not isinstance(patches,dict):raise ValidationError('Некорректный список связей.')
    state=changed_state(scenario,post)
    for gid,values in patches.items():
        group=directory_group(scenario,user,gid,lock=True)
        if not isinstance(values,dict) or set(values)!={'related_groups','replacement_groups'}:raise ValidationError('Некорректный список связей.')
        data=QueryDict('',mutable=True);data['notes']=group.notes
        for key,ids in values.items():
            if not isinstance(ids,list) or any(not isinstance(pk,str) or not pk.isdecimal() for pk in ids):raise ValidationError('Некорректный список связей.')
            data.setlist(key,ids)
        form=GroupSettingsForm(data,instance=group,user=user)
        if not form.is_valid():raise ValidationError('Выберите доступные группы. Группа не может ссылаться на себя.')
        form.save()
        entry=state['groups'].get(gid) or state.get('archived_groups',{}).get(gid)
        if entry:
            for field,snapshot_key in [('related_groups','related'),('replacement_groups','replacements')]:
                entry['ref'][snapshot_key]=list(groups_for(user,getattr(group,field).all()).values('pk','name'))
    persist(scenario,user,post.get('revision'),state,'Сохранён состав плана и связи групп' if patches else 'Изменён состав групп и каналов')
