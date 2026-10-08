"""Portal roles and access checks shared by views and navigation."""
from django.contrib.auth.models import Group, Permission

ADMIN = 'Администратор'
DIRECTOR = 'Генеральный директор'
LOGISTICIAN = 'Логист'
ROLES = (ADMIN, DIRECTOR, LOGISTICIAN)


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
    return role == ADMIN or (project == 'purchases' and role == LOGISTICIAN) or (project == 'motivation' and role == DIRECTOR)


def portal_access(request):
    return {'portal_is_admin': can_access(request.user, 'admin'),
            'portal_can_purchases': can_access(request.user, 'purchases'),
            'portal_can_motivation': can_access(request.user, 'motivation'),
            'portal_role_label': role_for(request.user) or 'Роль не назначена'}
