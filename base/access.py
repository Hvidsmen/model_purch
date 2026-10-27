"""Portal roles and access checks shared by views and navigation."""
from django.contrib.auth.models import Group, Permission

ADMIN = 'Администратор'
DIRECTOR = 'Генеральный директор'
LOGISTICIAN = 'Логист'
POLITICIAN = 'Политик'
SALES = 'Продажи'
MOTIVATION_VIEWER = 'Смотрящий'
FINANCIER = 'Финансист'
ANALYST = 'Аналитик'
MODELER = 'Моделятор'
MOTIVATION_READONLY_ROLES = (MOTIVATION_VIEWER, FINANCIER)
PERSONAL_ROLES = (POLITICIAN, SALES)
DEPUTY_DIRECTOR = 'Зам. директора'
BRANCH_DIRECTOR = 'Директор'
REPORT_ROLES = (*PERSONAL_ROLES, DEPUTY_DIRECTOR, BRANCH_DIRECTOR)
ROLES = (ADMIN, DIRECTOR, LOGISTICIAN, *PERSONAL_ROLES, DEPUTY_DIRECTOR, BRANCH_DIRECTOR, MOTIVATION_VIEWER, FINANCIER, ANALYST, MODELER)


def ensure_roles(**kwargs):
    for name in ROLES:
        group, _ = Group.objects.get_or_create(name=name)
        if name == ADMIN:
            group.permissions.set(Permission.objects.all())


def legacy_role_for(user):
    if not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        return ADMIN
    if not hasattr(user, '_portal_role'):
        names = list(user.groups.filter(name__in=ROLES).values_list('name', flat=True))
        inherited = set(user.groups.filter(portal_scope__isnull=False).exclude(portal_scope__portal_role='').values_list('portal_scope__portal_role', flat=True))
        user._portal_role = names[0] if len(names) == 1 else next(iter(inherited)) if not names and len(inherited) == 1 and inherited <= set(ROLES) else None
    return user._portal_role


PROJECT_ROLES = {
    'motivation': (ADMIN, MODELER, DIRECTOR, *REPORT_ROLES, *MOTIVATION_READONLY_ROLES),
    'purchases': (ADMIN, LOGISTICIAN, ANALYST),
    'dashboards': (ADMIN, ANALYST),
}
PROJECT_LABELS = {'motivation': 'Мотивация', 'purchases': 'Модель закупок', 'dashboards': 'Дашборды'}


def legacy_project_role(legacy, project):
    # Existing legacy analysts belong to dashboards. Procurement viewing is an
    # explicit new grant, not additional access for all existing analysts.
    if project=='purchases' and legacy==ANALYST:return None
    return legacy if legacy in PROJECT_ROLES[project] else None


def role_for(user, project=None):
    legacy = legacy_role_for(user)
    if project is None or legacy == ADMIN:
        return legacy
    if not user.is_authenticated or not user.is_active or project not in PROJECT_ROLES:
        return None
    from .models import UserProjectAccess, GroupProjectAccess
    cached = getattr(user, '_prefetched_objects_cache', {})
    personal = next((row for row in cached['project_access'] if row.project == project), None) if 'project_access' in cached else UserProjectAccess.objects.filter(user=user, project=project).first()
    if personal is not None:
        return personal.role if personal.role in PROJECT_ROLES[project] else None
    if 'groups' in cached and all('project_access' in getattr(group, '_prefetched_objects_cache', {}) for group in cached['groups']):
        inherited = [grant.role for group in cached['groups'] for grant in group.project_access.all() if grant.project == project]
    else:
        inherited = list(GroupProjectAccess.objects.filter(group__user=user, project=project).values_list('role', flat=True))
    if inherited:
        roles = {role for role in inherited if role}
        return next(iter(roles)) if len(roles) == 1 and roles <= set(PROJECT_ROLES[project]) else None
    return legacy_project_role(legacy,project)


def can_access(user, project):
    if project == 'admin':
        return role_for(user) == ADMIN
    if project == 'motivation_editor':
        return role_for(user, 'motivation') in (ADMIN, MODELER)
    if project.endswith('_admin'):
        return role_for(user, project[:-6]) == ADMIN
    return role_for(user, project) is not None


def project_role_summary(user):
    return ' · '.join(PROJECT_LABELS[project] + ': ' + role for project in PROJECT_ROLES if (role := role_for(user, project))) or 'Без доступа'



def portal_access(request):
    from django.conf import settings
    motivation_role = role_for(request.user, 'motivation')
    purchases_role = role_for(request.user,'purchases')
    active_project = 'motivation' if request.path.startswith('/admin_motivation') else 'purchases' if request.path.startswith('/model_purch/') else 'dashboards' if request.path.startswith('/dashboards/') else None
    return {'portal_purchases_readonly': purchases_role==ANALYST, 'portal_can_edit_purchases': purchases_role in (ADMIN,LOGISTICIAN), 'portal_can_administer_dashboards': can_access(request.user, 'dashboards_admin'), 'portal_can_administer_motivation': can_access(request.user, 'motivation_admin'), 'ad_auth_enabled': settings.AD_AUTH_ENABLED, 'ad_forest_discovery': settings.AD_AUTH_METHOD == 'windows' and settings.AD_FOREST_DISCOVERY, 'ad_netbios_domain': settings.AD_NETBIOS_DOMAIN, 'ad_upn_suffix': settings.AD_UPN_SUFFIX, 'ad_https_ready': request.is_secure(), 'ad_password_login_ready': request.is_secure() or not settings.AD_REQUIRE_HTTPS, 'windows_auth_enabled': settings.WINDOWS_AUTH_ENABLED, 'portal_is_admin': can_access(request.user, 'admin'),
            'portal_can_dashboards': can_access(request.user, 'dashboards'),
            'portal_can_purchases': purchases_role is not None,
            'portal_can_motivation': can_access(request.user, 'motivation'),
            'portal_can_edit_motivation': can_access(request.user, 'motivation_editor'), 'portal_can_review_motivation': motivation_role in (ADMIN, MODELER, DIRECTOR, FINANCIER), 'portal_motivation_modeler': motivation_role == MODELER, 'portal_financier': motivation_role == FINANCIER, 'portal_motivation_landing_reports': motivation_role in (*REPORT_ROLES, FINANCIER),
            'portal_motivation_readonly': motivation_role in (*MOTIVATION_READONLY_ROLES, DIRECTOR), 'portal_can_view_motivation_settings': motivation_role in (ADMIN, MODELER, DIRECTOR, *MOTIVATION_READONLY_ROLES), 'portal_reports_only': motivation_role in REPORT_ROLES, 'portal_can_plan_summary': motivation_role in (ADMIN, MODELER, DIRECTOR, *MOTIVATION_READONLY_ROLES), 'portal_role_label': (role_for(request.user, active_project) or 'Нет доступа') if active_project else ADMIN if can_access(request.user, 'admin') else project_role_summary(request.user)}


def motivation_viewer_read_request(request):
    """The only POST allowed for a viewer is comparison of saved data."""
    if request.method in ('GET','HEAD','OPTIONS'):
        return True
    match=getattr(request,'resolver_match',None)
    if match is None:
        from django.urls import resolve, Resolver404
        try: match=resolve(request.path_info)
        except Resolver404: return False
    if request.method != 'POST' or not match or match.url_name != 'motivation_coefficient_comparison':
        return False
    import json
    try:
        data=json.loads(request.body)
        return isinstance(data,dict) and data.get('action','compare')=='compare' and not any(data.get(key) for key in ('changes','deleted','director_changes'))
    except (ValueError,TypeError,UnicodeDecodeError):
        return False


def financier_read_request(request):
    """Finance can only read approved branch settings, discount segments and reports."""
    match = request.resolver_match
    if match and match.url_name in {'motivation_reviews', 'motivation_review'}:
        return True
    allowed = {
        'index_admin_motivation', 'coeff_subdivisions_admin_motivation',
        'motivation_directors_local', 'motivation_discount_segments', 'motivation_coefficient_coverage',
        'motivation_reports', 'motivation_report', 'motivation_plan_summary', 'motivation_reports_next', 'motivation_report_next',
    }
    return request.method in ('GET', 'HEAD', 'OPTIONS') and bool(match and match.url_name in allowed)


def motivation_scopes(groups):
    from .models import PortalReportGroup, GroupProjectAccess
    assignments = dict(GroupProjectAccess.objects.filter(group__in=groups, project='motivation').values_list('group_id', 'role'))
    scopes = PortalReportGroup.objects.filter(group__in=groups)
    eligible = [scope.pk for scope in scopes if assignments.get(scope.group_id, scope.portal_role) in PROJECT_ROLES['motivation']]
    return PortalReportGroup.objects.filter(pk__in=eligible)


def motivation_editor_required(request):
    from django.core.exceptions import PermissionDenied
    if not can_access(request.user, 'motivation_editor') and not (request.method in ('GET', 'HEAD', 'OPTIONS') and can_access(request.user, 'motivation')):
        raise PermissionDenied
