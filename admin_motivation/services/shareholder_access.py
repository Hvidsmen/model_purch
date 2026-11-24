from base.access import ADMIN,DIRECTOR,FINANCIER,MOTIVATION_VIEWER,role_for


def can_view(user):
    return bool(user.is_authenticated and user.is_active and role_for(user,'motivation') in (ADMIN,DIRECTOR,FINANCIER,MOTIVATION_VIEWER))


def can_start(user):
    return can_view(user) and role_for(user,'motivation')==ADMIN
