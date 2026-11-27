"""Apply trusted ordinary-report permissions to every shareholder snapshot reader."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from django.core.exceptions import PermissionDenied
from base.access import role_for,BRANCH_DIRECTOR,DEPUTY_DIRECTOR,POLITICIAN
from base.report_access import politician_scope,allowed_values,worker_scope

ROLES=(BRANCH_DIRECTOR,DEPUTY_DIRECTOR,POLITICIAN)
current=ContextVar('shareholder_report_scope',default=None)
_basis=ContextVar('shareholder_execution_basis',default=False)
TAGS={'pol':'Политики','sales':'Продажи','dir':'Директора','cur':'Кураторы'}


class Scope:
    def __init__(self,user):
        self.user=user;self.rule=politician_scope(user);self._workers={}
        if not self.rule:raise PermissionDenied
        types=self.rule['type'];types=[types] if isinstance(types,str) else types
        self.kinds=[k for k in TAGS.values() if k in types]
        if not self.kinds:raise PermissionDenied

    def worker(self,kind):
        if kind in self._workers:return self._workers[kind]
        if kind not in self.kinds:raise PermissionDenied
        rule=worker_scope(self.rule,'manager-detail',{'TypeCoeff_':kind})
        branches=allowed_values(rule,kind)[2]
        self._workers[kind]=(rule,set(branches))
        return self._workers[kind]

    def permits(self,row,dataset='',kind=None,basis=False,run=None):
        kind=kind or TAGS.get(dataset.rsplit('_',1)[-1]) or row.get('ТипКоэф') or row.get('TypeCoeff')
        if dataset in ('deals','mapping','policy_plan_shares','policy_rates','policy_defaults'):kind='Политики'
        if kind not in self.kinds:return False
        rule,branches=self.worker(kind)
        branch=row.get('Подразделение') or row.get('Subdivision') or row.get('SourceBranch')
        if dataset=='deals' and branch=='ТЕРМОТРЕЙД' and run:
            closed=str(row.get('ClosedDate') or '')
            quarter=(int(closed[5:7])-1)//3+1 if len(closed)>=7 else None
            if run.scenario=='combined' or quarter==4:branch='Д-Москва'
        if branch not in branches:return False
        if basis or _basis.get() or dataset.startswith('dept_'):return True
        author=row.get('АвторСделки') or row.get('AssignedAuthor') or row.get('Author_') or row.get('ScopeAuthor')
        if dataset.startswith('perf_') and not author:return True
        return bool(rule.get('all_authors') or author==rule.get('author'))

    def restrict(self,query):
        from django.core.exceptions import ValidationError
        if len(query.get('search',''))>255:raise ValidationError('Поисковый запрос слишком длинный.')
        query=query.copy()
        kinds=list(dict.fromkeys(query.getlist('type'))) or self.kinds
        if any(k not in self.kinds for k in kinds):raise PermissionDenied
        query.setlist('type',kinds)
        branches=set().union(*(self.worker(k)[1] for k in kinds))
        selected=query.getlist('subdivision')
        if any(b not in branches for b in selected):raise PermissionDenied
        query.setlist('subdivision',selected or sorted(branches))
        forced=[]
        for kind in kinds:
            rule,_=self.worker(kind)
            if rule.get('all_authors'):break
            forced.append(rule['author'])
        else:
            authors=query.getlist('author')
            if any(a not in forced for a in authors):raise PermissionDenied
            query.setlist('author',authors or list(dict.fromkeys(forced)))
        return query


def for_user(user):
    return Scope(user) if role_for(user,'motivation') in ROLES else None


def scoped_request(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        token=current.set(for_user(request.user))
        try:return view(request,*args,**kwargs)
        finally:current.reset(token)
    return wrapped


@contextmanager
def execution_basis():
    """Internal full-branch execution totals; branch/type rights still apply."""
    token=_basis.set(True)
    try:yield
    finally:_basis.reset(token)


def permits(row,dataset='',kind=None,run=None):
    scope=current.get()
    return scope is None or scope.permits(row,dataset,kind,run=run)


def query(query):
    scope=current.get()
    return scope.restrict(query) if scope else query.copy()


def report_kinds(user):
    if role_for(user,'motivation') not in ROLES:return ('team','mine','detail','deals','registry','executive')
    from .reports_next import available
    return tuple(k for k in available(user) if k in ('team','mine','detail','deals','registry'))
