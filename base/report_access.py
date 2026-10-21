"""Server-side report restrictions; never derive identity from query parameters."""
from django.core.exceptions import PermissionDenied
from .access import role_for, POLITICIAN
from .models import MotivationReportAccess, PortalReportGroup

PERSONAL_REPORTS = ('manager', 'manager-detail', 'deals')


def politician_scope(user):
    if role_for(user) != POLITICIAN:
        return None
    access = MotivationReportAccess.objects.select_related('subdivision__merged_into').filter(user=user).first()
    if not access or not access.deal_author.strip():
        return None
    groups = list(PortalReportGroup.objects.filter(group__user=user).select_related('employee_type').prefetch_related('subdivisions__merged_into'))
    if groups:
        rules = []
        for group in groups:
            names = sorted({(branch.merged_into or branch).subdivision_key for branch in group.subdivisions.all()})
            worker = group.employee_type.type_coeff_name.strip()
            if names and worker:
                rules.append({'type': worker, 'subdivisions': names})
        if not rules:
            return None
        return {'subdivision': sorted({name for rule in rules for name in rule['subdivisions']}),
                'type': sorted({rule['type'] for rule in rules}), 'author': access.deal_author.strip(), 'rules': rules}
    subdivision = access.subdivision.merged_into or access.subdivision
    return {'subdivision': subdivision.subdivision_key, 'author': access.deal_author.strip(), 'type': 'Политики'}


def allowed_values(scope, worker=None):
    types = scope['type'] if isinstance(scope['type'], list) else [scope['type']]
    worker = worker if worker in types else 'Политики' if 'Политики' in types else types[0]
    if scope.get('rules'):
        branches = sorted({name for rule in scope['rules'] if rule['type'] == worker for name in rule['subdivisions']})
    else:
        branches = scope['subdivision'] if isinstance(scope['subdivision'], list) else [scope['subdivision']]
    if not branches:
        raise PermissionDenied
    return types, worker, branches


def scoped_values(kind, values, scope):
    if not scope:
        return values
    if kind not in PERSONAL_REPORTS or any(not scope.get(key) for key in ('subdivision', 'author', 'type')):
        raise PermissionDenied
    from admin_motivation.services.reports import FILTERS
    result = dict(values)
    type_key, branch_key = ('TypeCoeff', 'Division') if kind == 'deals' else ('TypeCoeff_', 'Subdivision')
    types, worker, branches = allowed_values(scope, values.get(type_key))
    selected = values.get(branch_key)
    selected = selected if isinstance(selected, list) else [selected]
    selected = list(dict.fromkeys(value for value in selected if value in branches)) or branches
    for name, parameter, caption, dataset, multiple in FILTERS[kind]:
        if name == 'type':
            result[parameter] = worker
        elif name == 'subdivision':
            result[parameter] = selected if multiple else selected[0]
        elif name == 'author':
            result[parameter] = [scope['author']] if multiple else scope['author']
    return result


def scoped_query(query, scope, kind='manager-detail'):
    from admin_motivation.services.reports import FILTERS
    query = query.copy()
    # Avoid date validation here; the report view supplies default deal dates.
    values = {}
    for name, parameter, label, dataset, multiple in FILTERS[kind]:
        selected = query.getlist(name)
        values[parameter] = selected if multiple else selected[0] if selected else None
    values = scoped_values(kind, values, scope)
    for name, parameter, label, dataset, multiple in FILTERS[kind]:
        if name in ('type', 'subdivision', 'author'):
            value = values[parameter]
            query.setlist(name, value if isinstance(value, list) else [value])
    return query


def identity_choices(kind, values, scope):
    from admin_motivation.services.reports import FILTERS
    type_key = 'TypeCoeff' if kind == 'deals' else 'TypeCoeff_'
    types, worker, branches = allowed_values(scope, values.get(type_key))
    allowed = {'type': types, 'subdivision': branches, 'author': [scope['author']]}
    result = {}
    for name, parameter, caption, dataset, multiple in FILTERS[kind]:
        if name not in allowed:
            continue
        selected = values.get(parameter)
        selected = selected if isinstance(selected, list) else [selected]
        options = allowed[name]
        result[name] = {'locked': len(options) == 1, 'locked_value': options[0] if len(options) == 1 else None,
                       'options': [{'value': value, 'selected': value in selected} for value in options]}
    return result
