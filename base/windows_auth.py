"""Windows identity supplied by IIS/FastCGI, never by an HTTP identity header."""
from django.conf import settings
from django.contrib.auth.backends import RemoteUserBackend


class WindowsRemoteUserBackend(RemoteUserBackend):
    def authenticate(self, request, remote_user):
        if not settings.WINDOWS_AUTH_ENABLED or not isinstance(remote_user, str):
            return None
        parts = remote_user.split('\\')
        if len(parts) != 2:
            return None
        domain, username = parts
        if not domain or not username or any(character.isspace() for character in remote_user):
            return None
        if domain.casefold() not in {value.casefold() for value in settings.WINDOWS_AUTH_DOMAINS}:
            return None
        if len(remote_user) > 150 or any(ord(character) < 32 for character in remote_user):
            return None
        return super().authenticate(request, remote_user=remote_user)

    def clean_username(self, username):
        # Keep the domain to avoid linking a Windows identity to a local account.
        return username.casefold()

    def configure_user(self, request, user, created=True):
        if created:
            user.set_unusable_password()
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=['password', 'is_staff', 'is_superuser'])
        return user
