"""Portal roles and access checks shared by views and navigation."""
from django.contrib.auth.models import Group, Permission

ADMIN = 'Администратор'
DIRECTOR = 'Генеральный директор'
LOGISTICIAN = 'Логист'
POLITICIAN = 'Политик'
ROLES = (ADMIN, DIRECTOR, LOGISTICIAN, POLITICIAN)


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
        user._portal_role = names[0] if len(names) == 1 else None
    return user._portal_role


def can_access(user, project):
    role = role_for(user)
    return role == ADMIN or (project == 'purchases' and role == LOGISTICIAN) or (project == 'motivation' and role in (DIRECTOR, POLITICIAN))


def portal_access(request):
    from django.conf import settings
    return {'ad_auth_enabled': settings.AD_AUTH_ENABLED, 'ad_forest_discovery': settings.AD_AUTH_METHOD == 'windows' and settings.AD_FOREST_DISCOVERY, 'ad_netbios_domain': settings.AD_NETBIOS_DOMAIN, 'ad_upn_suffix': settings.AD_UPN_SUFFIX, 'ad_https_ready': request.is_secure(), 'ad_password_login_ready': request.is_secure() or not settings.AD_REQUIRE_HTTPS, 'windows_auth_enabled': settings.WINDOWS_AUTH_ENABLED, 'portal_is_admin': can_access(request.user, 'admin'),
            'portal_can_purchases': can_access(request.user, 'purchases'),
            'portal_can_motivation': can_access(request.user, 'motivation'),
            'portal_reports_only': role_for(request.user) == POLITICIAN, 'portal_role_label': role_for(request.user) or 'Роль не назначена'}
