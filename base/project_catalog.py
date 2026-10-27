"""Project selectors for the administration catalogs; never changes access grants."""
from copy import copy
from urllib.parse import urlencode
from django.urls import reverse
from .access import PROJECT_LABELS, PROJECT_ROLES, ADMIN, role_for, legacy_project_role


def group_roles(group):
    assignments = {row.project: row.role for row in group.project_access.all()}
    scope = getattr(group, 'portal_scope', None)
    legacy = scope.portal_role if scope else ''
    if legacy == ADMIN:
        return {project: ADMIN for project in PROJECT_ROLES}
    return {project: role for project, allowed in PROJECT_ROLES.items()
            if (role := assignments.get(project, legacy_project_role(legacy,project))) in allowed}


def assigned_roles(user):
    # Disabled accounts still belong in the administration catalog. Do not change
    # role_for's authentication behavior or mutate the user instance itself.
    account = copy(user)
    account.is_active = True
    if hasattr(account, '_portal_role'):
        del account._portal_role
    return {project: role for project in PROJECT_ROLES if (role := role_for(account, project))}


def selector(request, route):
    project = request.GET.get('project', '')
    if project not in PROJECT_ROLES:
        project = ''
    tabs = []
    for key, label in [('', 'Все проекты'), *PROJECT_LABELS.items()]:
        params = {'project': key} if key else {}
        if request.GET.get('q'):
            params['q'] = request.GET['q'][:150]
        tabs.append({'label': label, 'active': key == project,
                     'url': reverse(route) + ('?' + urlencode(params) if params else '')})
    return {'selected_project': project, 'project_tabs': tabs,
            'project_query': '?' + urlencode({'project': project}) if project else ''}
