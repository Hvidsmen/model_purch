"""Server-side report restrictions; never derive identity from query parameters."""
from django.core.exceptions import PermissionDenied
from .access import role_for, POLITICIAN
from .models import MotivationReportAccess

PERSONAL_REPORTS = ('manager', 'manager-detail', 'deals')


def politician_scope(user):
    if role_for(user) != POLITICIAN:
        return None
    access = MotivationReportAccess.objects.select_related('subdivision__merged_into').filter(user=user).first()
    if not access or not access.deal_author.strip():
        return None
    subdivision = access.subdivision.merged_into or access.subdivision
    return {'subdivision': subdivision.subdivision_key, 'author': access.deal_author.strip(), 'type': 'Политики'}


def scoped_values(kind, values, scope):
    if not scope:
        return values
    if kind not in PERSONAL_REPORTS or any(not scope.get(key) for key in ('subdivision', 'author', 'type')):
        raise PermissionDenied
    from admin_motivation.services.reports import FILTERS
    result = dict(values)
    fixed = {'type': scope['type'], 'subdivision': scope['subdivision'], 'author': scope['author']}
    for name, parameter, caption, dataset, multiple in FILTERS[kind]:
        if name in fixed:
            result[parameter] = [fixed[name]] if multiple else fixed[name]
    return result


def scoped_query(query, scope):
    query = query.copy()
    for key in ('type', 'subdivision', 'author'):
        value = scope[key]
        query.setlist(key, [value])
    return query
