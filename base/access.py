"""Portal roles and access checks shared by views and navigation."""
from django.contrib.auth.models import Group, Permission

ADMIN = 'Администратор'
DIRECTOR = 'Генеральный директор'
LOGISTICIAN = 'Логист'
POLITICIAN = 'Политик'
SALES = 'Продажи'
MOTIVATION_VIEWER = 'Смотрящий'
FINANCIER = 'Финансист'
MOTIVATION_READONLY_ROLES = (MOTIVATION_VIEWER, FINANCIER)
PERSONAL_ROLES = (POLITICIAN, SALES)
DEPUTY_DIRECTOR = 'Зам. директора'
BRANCH_DIRECTOR = 'Директор'
REPORT_ROLES = (*PERSONAL_ROLES, DEPUTY_DIRECTOR, BRANCH_DIRECTOR)
ROLES = (ADMIN, DIRECTOR, LOGISTICIAN, *PERSONAL_ROLES, DEPUTY_DIRECTOR, BRANCH_DIRECTOR, MOTIVATION_VIEWER, FINANCIER)


def ensure_roles(**kwargs):
    for name in ROLES:
        group, _ = Group.objects.get_or_create(name=name)
        if name == ADMIN:
            group.permissions.set(Permission.objects.all())


def role_for(user):
    if not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        return ADMIN
    if not hasattr(user, '_portal_role'):
        names = list(user.groups.filter(name__in=ROLES).values_list('name', flat=True))
        inherited = set(user.groups.filter(portal_scope__isnull=False).values_list('portal_scope__portal_role', flat=True))
        user._portal_role = names[0] if len(names) == 1 else next(iter(inherited)) if not names and len(inherited) == 1 and inherited <= set(ROLES) else None
    return user._portal_role


def can_access(user, project):
    role = role_for(user)
    return role == ADMIN or (project == 'purchases' and role == LOGISTICIAN) or (project == 'motivation' and role in (DIRECTOR, *MOTIVATION_READONLY_ROLES, *REPORT_ROLES))


def portal_access(request):
    from django.conf import settings
    return {'ad_auth_enabled': settings.AD_AUTH_ENABLED, 'ad_forest_discovery': settings.AD_AUTH_METHOD == 'windows' and settings.AD_FOREST_DISCOVERY, 'ad_netbios_domain': settings.AD_NETBIOS_DOMAIN, 'ad_upn_suffix': settings.AD_UPN_SUFFIX, 'ad_https_ready': request.is_secure(), 'ad_password_login_ready': request.is_secure() or not settings.AD_REQUIRE_HTTPS, 'windows_auth_enabled': settings.WINDOWS_AUTH_ENABLED, 'portal_is_admin': can_access(request.user, 'admin'),
            'portal_can_purchases': can_access(request.user, 'purchases'),
            'portal_can_motivation': can_access(request.user, 'motivation'),
            'portal_financier': role_for(request.user) == FINANCIER, 'portal_motivation_landing_reports': role_for(request.user) in (*REPORT_ROLES, FINANCIER),
            'portal_motivation_readonly': role_for(request.user) in MOTIVATION_READONLY_ROLES, 'portal_can_view_motivation_settings': role_for(request.user) in (ADMIN,*MOTIVATION_READONLY_ROLES), 'portal_reports_only': role_for(request.user) in REPORT_ROLES, 'portal_can_plan_summary': role_for(request.user) in (ADMIN, DIRECTOR, *MOTIVATION_READONLY_ROLES), 'portal_role_label': role_for(request.user) or 'Роль не назначена'}


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
    allowed = {
        'index_admin_motivation', 'coeff_subdivisions_admin_motivation',
        'motivation_directors_local', 'motivation_discount_segments',
        'motivation_reports', 'motivation_report', 'motivation_plan_summary',
    }
    return request.method in ('GET', 'HEAD', 'OPTIONS') and bool(match and match.url_name in allowed)
