from base.access import ADMIN,DIRECTOR,FINANCIER,MOTIVATION_VIEWER,BRANCH_DIRECTOR,DEPUTY_DIRECTOR,POLITICIAN,role_for


def can_view(user):
    if not user.is_authenticated or not user.is_active:return False
    role=role_for(user,'motivation')
    if role in (ADMIN,DIRECTOR,FINANCIER,MOTIVATION_VIEWER):return True
    if role in (BRANCH_DIRECTOR,DEPUTY_DIRECTOR,POLITICIAN):
        from base.report_access import politician_scope
        return bool(politician_scope(user))
    return False


def can_start(user):
    return can_view(user) and role_for(user,'motivation')==ADMIN
