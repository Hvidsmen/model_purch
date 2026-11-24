"""Calculation coverage, with an independent switch for the temporary expansion."""
SUBDIVISION = 'Д-Москва'
VIEW = 'DataWH.motivation.PortalCalculationSubdivisions'


def predicate(expression):
    from .main_quarter_mode import all_subdivisions
    if all_subdivisions():return '1=1'
    return expression + "=N'" + SUBDIVISION.replace("'", "''") + "'"


def restrict_report(query, scope):
    """Keep the report and its export within the approved calculation scope."""
    from django.core.exceptions import PermissionDenied, ValidationError
    if scope and scope.get('curator'):return
    if query.getlist('type')==['Кураторы']:return
    from .subdivision_aliases import canonical, mapping
    names = mapping()
    if scope:
        allowed = scope.get('subdivision', [])
        allowed = [allowed] if isinstance(allowed, str) else allowed
        if SUBDIVISION not in [canonical(value, names) for value in allowed]:
            raise PermissionDenied
    if any(canonical(value, names) != SUBDIVISION
           for value in query.getlist('subdivision') if value):
        raise ValidationError('Этот расчёт доступен только по объединённому подразделению Д-Москва.')
    query.setlist('subdivision', [SUBDIVISION])

