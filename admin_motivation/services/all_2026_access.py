"""Explicit read access to the additional 2026 premium report."""
from base.access import ADMIN, DIRECTOR, BRANCH_DIRECTOR, role_for


def can_view(user):
    return bool(user.is_authenticated and user.is_active
                and role_for(user, 'motivation') in (ADMIN, DIRECTOR, BRANCH_DIRECTOR))
