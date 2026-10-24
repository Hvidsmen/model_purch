"""Server-side report restrictions; never derive identity from query parameters."""
from django.core.exceptions import PermissionDenied
from .access import role_for, POLITICIAN, DEPUTY_DIRECTOR, BRANCH_DIRECTOR, REPORT_ROLES, SALES
from .models import MotivationReportAccess, PortalReportGroup

PERSONAL_REPORTS = ('manager', 'manager-detail', 'deals')


def report_kinds(user):
    return PERSONAL_REPORTS


def politician_scope(user):
    scope = _politician_scope(user)
    if scope and role_for(user) in (POLITICIAN, DEPUTY_DIRECTOR, BRANCH_DIRECTOR):
        scope['pilot_periods'] = True
    return scope


def _politician_scope(user):
    role = role_for(user)
    if role not in REPORT_ROLES:
        return None
    if role in (DEPUTY_DIRECTOR, BRANCH_DIRECTOR):
        from admin_motivation.models import TypeCoeff
        groups = PortalReportGroup.objects.filter(group__user=user).prefetch_related('subdivisions__merged_into')
        branches = sorted({(branch.merged_into or branch).subdivision_key for group in groups for branch in group.subdivisions.all()})
        types = ['Политики'] if role == DEPUTY_DIRECTOR else sorted({name.strip() for name in TypeCoeff.objects.values_list('type_coeff_name', flat=True) if name and name.strip()})
        if not branches or not types:
            return None
        result = {'subdivision': branches, 'type': types, 'all_authors': True, 'reports': report_kinds(user)}
        if role == DEPUTY_DIRECTOR and user.last_name.strip() and user.first_name.strip():
            # Use the trusted user identity, never a name supplied in the report URL.
            author = ' '.join(f'{user.last_name} {user.first_name}'.split())
            from admin_motivation.models import DirectorEmployee
            matches = [employee.full_name for employee in DirectorEmployee.objects.filter(is_archived=False)
                       if ' '.join(employee.full_name.split()).casefold() == author.casefold()]
            result['director_author'] = matches[0] if matches else author
            result['type'] = [*types, 'Директора']
        return result
    access = MotivationReportAccess.objects.select_related('subdivision__merged_into').filter(user=user).first()
    if not access or not access.deal_author.strip():
        return None
    groups = list(PortalReportGroup.objects.filter(group__user=user).select_related('employee_type').prefetch_related('subdivisions__merged_into'))
    if groups:
        rules = []
        for group in groups:
            names = sorted({(branch.merged_into or branch).subdivision_key for branch in group.subdivisions.all()})
            worker = group.employee_type.type_coeff_name.strip() if group.employee_type_id else ('Продажи' if role == SALES else 'Политики')
            if names and worker:
                rules.append({'type': worker, 'subdivisions': names})
        if not rules:
            return None
        return {'subdivision': sorted({name for rule in rules for name in rule['subdivisions']}),
                'type': sorted({rule['type'] for rule in rules}), 'author': access.deal_author.strip(), 'rules': rules}
    subdivision = access.subdivision.merged_into or access.subdivision
    return {'subdivision': subdivision.subdivision_key, 'author': access.deal_author.strip(), 'type': 'Продажи' if role == SALES else 'Политики'}


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


def worker_scope(scope, kind, values):
    """Deputies see all politicians, but only their own director compensation."""
    if not scope or not scope.get('director_author'):
        return scope
    parameter = 'TypeCoeff' if kind == 'deals' else 'type_coeff' if kind == 'subdivisions' else 'TypeCoeff_'
    _, worker, _ = allowed_values(scope, values.get(parameter))
    if worker == 'Директора':
        return {**scope, 'all_authors': False, 'author': scope['director_author']}
    return scope


def scoped_values(kind, values, scope):
    if not scope:
        return values
    scope = worker_scope(scope, kind, values)
    if kind not in scope.get('reports', PERSONAL_REPORTS) or any(not scope.get(key) for key in ('subdivision', 'type')) or (not scope.get('all_authors') and not scope.get('author')):
        raise PermissionDenied
    from admin_motivation.services.reports import FILTERS
    result = dict(values)
    type_key, branch_key = ('TypeCoeff', 'Division') if kind == 'deals' else ('type_coeff', 'Subdivision') if kind == 'subdivisions' else ('TypeCoeff_', 'Subdivision')
    types, worker, branches = allowed_values(scope, values.get(type_key))
    selected = values.get(branch_key)
    selected = selected if isinstance(selected, list) else [selected]
    selected = list(dict.fromkeys(value for value in selected if value in branches)) or branches
    for name, parameter, caption, dataset, multiple in FILTERS[kind]:
        if name == 'type':
            result[parameter] = ([value for value in (values.get(parameter) or []) if value in types] or types) if multiple else worker
        elif name == 'subdivision':
            result[parameter] = selected if multiple else selected[0]
        elif name == 'author':
            result[parameter] = values.get(parameter) if scope.get('all_authors') else [scope['author']] if multiple else scope['author']
    from admin_motivation.services.report_periods import pilot_enabled, allowed_period, PILOT_QUARTER
    if pilot_enabled(kind, scope) and kind == 'manager' and not allowed_period(result.get('qm')):
        result['qm'] = PILOT_QUARTER
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
        if name in ('type', 'subdivision', 'author') or (name == 'quarter' and scope.get('pilot_periods')):
            value = values[parameter]
            if value is None or value == []:
                query.pop(name, None)
            else:
                query.setlist(name, value if isinstance(value, list) else [value])
    return query


def identity_choices(kind, values, scope):
    scope = worker_scope(scope, kind, values)
    from admin_motivation.services.reports import FILTERS
    type_key = 'TypeCoeff' if kind == 'deals' else 'type_coeff' if kind == 'subdivisions' else 'TypeCoeff_'
    types, worker, branches = allowed_values(scope, values.get(type_key))
    allowed = {'type': types, 'subdivision': branches}
    if not scope.get('all_authors'):
        allowed['author'] = [scope['author']]
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
