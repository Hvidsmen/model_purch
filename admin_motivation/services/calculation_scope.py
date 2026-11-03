"""The two portal calculations currently run for Moscow only."""
SUBDIVISION = 'Д-Москва'
VIEW = 'DataWH.motivation.PortalCalculationSubdivisions'


def predicate(expression):
    return expression + "=N'" + SUBDIVISION.replace("'", "''") + "'"
