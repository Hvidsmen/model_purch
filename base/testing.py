"""Authenticated administrator fixture for existing functional tests."""
from django.contrib.auth.models import User


def authorize_admin(client):
    user, created = User.objects.get_or_create(username='portal-test-admin', defaults={'is_staff': True, 'is_superuser': True})
    if created:
        user.set_unusable_password()
        user.save()
    client.force_login(user)
    return user


def authorize_test_case(case):
    if hasattr(case, 'client'):
        return authorize_admin(case.client)
