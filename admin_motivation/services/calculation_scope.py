"""The two portal calculations currently run for Moscow only."""
SUBDIVISION = 'Д-Москва'
VIEW = 'DataWH.motivation.PortalCalculationSubdivisions'


def predicate(expression):
    return expression + "=N'" + SUBDIVISION.replace("'", "''") + "'"


def restrict_report(query, scope):
    """Keep the report and its export within the approved calculation scope."""
    from django.core.exceptions import PermissionDenied, ValidationError
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

