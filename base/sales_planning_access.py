"""Fail-closed sales-plan-group boundaries for the shared Split workspace."""
from collections import defaultdict
from django.core.exceptions import PermissionDenied
from django.db.models.functions import Trim
from .access import ADMIN,PLANNER,role_for
from .models import UserProjectAccess,GroupProjectAccess,SalesPlanningGoods
from .sales_planning_scope import split_goods,split_groups
from .sales_planning_group_service import group_key


def scopes(user):
    role=role_for(user,'sales_planning')
    if role==ADMIN:return None
    if role!=PLANNER:raise PermissionDenied
    own=UserProjectAccess.objects.filter(user=user,project='sales_planning').first()
    grants=[own] if own is not None else GroupProjectAccess.objects.filter(group__user=user,project='sales_planning',role=PLANNER)
    values={str(value).strip() for grant in grants for value in (grant.sales_plan_groups if isinstance(grant.sales_plan_groups,list) else []) if isinstance(value,str) and value.strip()}
    if not values:raise PermissionDenied('Планировщику не назначены группы планов продаж.')
    return frozenset(values)


def require(user):
    return scopes(user)


def goods_for(user,queryset=None):
    allowed=scopes(user);queryset=split_goods(include_inactive=True) if queryset is None else queryset
    return queryset if allowed is None else queryset.annotate(planner_sales_group=Trim('sales_plan_group')).filter(planner_sales_group__in=allowed)


def group_map(user):
    if not hasattr(user,'_sales_group_scope_map'):
        result=defaultdict(set)
        for name,category in split_goods(include_inactive=True).values_list('planning_group','sales_plan_group').iterator():result[group_key(name)].add(str(category or '').strip())
        user._sales_group_scope_map=dict(result)
    return user._sales_group_scope_map


def groups_for(user,queryset=None):
    allowed=scopes(user);queryset=split_groups() if queryset is None else queryset
    if allowed is None:return queryset
    permitted=[key for key,values in group_map(user).items() if values and '' not in values and values<=allowed]
    return queryset.filter(source_key__in=permitted)


def group_allowed(user,group,allowed):
    if allowed is None:return True
    # Prefer snapshot model identities; every model must still belong to the
    # planner's responsibility, including inactive models and archived inputs.
    models=[model for power in group.get('powers',{}).values() for model in power.get('models',[])]
    if any(not str(model.get('id','')).isdecimal() for model in models):return False
    ids={int(model['id']) for model in models}
    if ids:
        if not hasattr(user,'_sales_model_scope_map'):user._sales_model_scope_map=dict(SalesPlanningGoods.objects.values_list('pk','sales_plan_group'))
        known=user._sales_model_scope_map
        if any(str(known.get(pk) or '').strip() not in allowed for pk in ids):return False
    values=group_map(user).get(group.get('ref',{}).get('source_key'),set())
    if not values:
        raw=group.get('ref',{}).get('details',{}).get('sales_plan_group','')
        values={value.strip() for value in str(raw).split(',') if value.strip()}
    return bool(values) and '' not in values and values<=allowed


def scenario_allowed(user,scenario,state=None):
    allowed=scopes(user)
    if allowed is None:return True
    state=scenario.state if state is None else state
    groups=[*state.get('groups',{}).values(),*state.get('archived_groups',{}).values()]
    if groups:return all(group_allowed(user,group,allowed) for group in groups)
    assigned=set(state.get('sales_responsibility_groups',[]))
    return bool(assigned) and assigned<=allowed


def check_scenario(user,scenario,state=None):
    if not scenario_allowed(user,scenario,state):raise PermissionDenied('Сценарий содержит группы за пределами вашей ответственности.')
    allowed=scopes(user)
    scenario._sales_restricted=allowed is not None
    if allowed is not None:scenario._sales_allowed_group_ids=set(map(str,groups_for(user).values_list('pk',flat=True)))


def scenarios_for(user,queryset):
    if scopes(user) is None:return queryset
    ids=[item.pk for item in queryset.only('pk','state').iterator(chunk_size=20) if scenario_allowed(user,item)]
    return queryset.filter(pk__in=ids)
