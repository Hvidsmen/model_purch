"""One version-independent motivation scope; immutable during each operation."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib,json

DEFAULT_EXCLUDED = ('E-Com','Retail','Брендинг','Прочее','СНГ','Д-Маврикий','Буфер','Вентиляция')
POLICY = 'motivation-subdivisions-v2-directory'
_current = ContextVar('motivation_subdivision_exclusions', default=None)
SCOPE_MARKER = '__MOTIVATION_SUBDIVISION_SCOPE__'


def excluded_names():
    # Pure rule helpers can be used with the initial dictionary without DB I/O.
    # All portal requests and calculation/import entry points establish a DB snapshot.
    snapshot = _current.get()
    return DEFAULT_EXCLUDED if snapshot is None else snapshot


@contextmanager
def exclusion_scope():
    if _current.get() is not None:
        yield excluded_names()
        return
    from ..models import MotivationSubdivisionExclusion
    names = tuple(MotivationSubdivisionExclusion.objects.filter(enabled=True).values_list('subdivision_name', flat=True))
    token = _current.set(names)
    try:
        yield names
    finally:
        _current.reset(token)


def with_exclusions(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with exclusion_scope():
            return function(*args, **kwargs)
    return wrapped


def policy_signature():
    return POLICY + ':' + hashlib.sha256(json.dumps(sorted(n.casefold().strip() for n in excluded_names()),ensure_ascii=False).encode()).hexdigest()


class ExcludedNames:
    def __iter__(self):return iter(excluded_names())
EXCLUDED = ExcludedNames()


def included(name, aliases=None):
    aliases = aliases or {}
    raw = str(name or '').strip()
    target = str(aliases.get(raw, raw) or '').strip()
    excluded = {value.strip().casefold() for value in excluded_names()}
    return raw.casefold() not in excluded and target.casefold() not in excluded


def sql_predicate(expression):
    names = excluded_names()
    if not names:return '1=1'
    literals = ','.join("UPPER(N'" + value.strip().replace("'", "''") + "')" for value in sorted(names))
    return f"UPPER(LTRIM(RTRIM(COALESCE({expression},N'')))) NOT IN ({literals})"


def resolve_scope_sql(sql):
    return sql.replace(SCOPE_MARKER, sql_predicate('[Подразделение]'))


def excluded_plan_names(scenario):
    from .subdivision_aliases import mapping
    aliases = mapping()
    return {name for name in scenario.lines.exclude(subdivision='').order_by().values_list('subdivision', flat=True).distinct()
            if not included(name, aliases)}
