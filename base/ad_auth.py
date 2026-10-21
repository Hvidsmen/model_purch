"""AD form login via native Windows validation or certificate-verified LDAPS."""
import logging
import ssl
from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied
from django.db import transaction

logger = logging.getLogger(__name__)


def directory_identity(username, password):
    # ldap3 is optional: ordinary local authentication needs no AD dependency.
    from ldap3 import Connection, Server, Tls, NONE, SUBTREE
    from ldap3.utils.conv import escape_filter_chars
    endpoint = urlsplit(settings.AD_LDAPS_URL)
    if endpoint.scheme != 'ldaps' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.path not in ('', '/') or endpoint.query or endpoint.fragment:
        raise ValueError('Configure a plain ldaps://hostname:636 endpoint')
    domain = settings.AD_NETBIOS_DOMAIN
    suffix = settings.AD_UPN_SUFFIX
    if '\\' in username:
        parts = username.split('\\')
        if len(parts) != 2 or parts[0].casefold() != domain.casefold():
            raise PermissionDenied
        account = parts[1]
        bind_name = domain + '\\' + account
        field = 'sAMAccountName'
    else:
        parts = username.split('@')
        if len(parts) != 2 or parts[1].casefold() != suffix.casefold():
            raise PermissionDenied
        account = username
        bind_name = username
        field = 'userPrincipalName'
    if not account or any(ord(character) < 32 or character.isspace() for character in account):
        raise PermissionDenied
    tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=settings.AD_CA_CERT_FILE or None, sni=endpoint.hostname)
    server = Server(endpoint.hostname, port=endpoint.port or 636, use_ssl=True,
                    tls=tls, get_info=NONE, connect_timeout=settings.AD_CONNECT_TIMEOUT)
    connection = None
    try:
        connection = Connection(server, user=bind_name, password=password, auto_referrals=False,
                                read_only=True, receive_timeout=settings.AD_CONNECT_TIMEOUT, raise_exceptions=True)
        if not connection.bind():
            raise PermissionDenied
        found = connection.search(settings.AD_SEARCH_BASE, '(' + field + '=' + escape_filter_chars(account) + ')',
                                  search_scope=SUBTREE, size_limit=2,
                                  attributes=['sAMAccountName', 'givenName', 'sn', 'mail', 'userAccountControl'])
        if not found or len(connection.entries) != 1:
            raise PermissionDenied
        entry = connection.entries[0].entry_attributes_as_dict
        def scalar(key):
            value = entry.get(key, [])
            return value[0] if isinstance(value, list) and value else value if not isinstance(value, list) else ''
        sam = str(scalar('sAMAccountName'))
        if not sam or any(character in sam for character in ('\\', '@')) or len(domain + '\\' + sam) > 150:
            raise PermissionDenied
        flags = scalar('userAccountControl')
        if flags == '' or int(flags) & 2:
            raise PermissionDenied
        return {'username': (domain + '\\' + sam).casefold(),
                'first_name': str(scalar('givenName'))[:150], 'last_name': str(scalar('sn'))[:150],
                'email': str(scalar('mail'))[:254]}
    finally:
        if connection is not None:
            # Bind passwords must not remain in a pooled connection.
            try:
                connection.unbind()
            except Exception:
                pass


class ActiveDirectoryBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not settings.AD_AUTH_ENABLED or not isinstance(username, str):
            return None
        # Unqualified names remain ordinary local portal accounts.
        if '\\' not in username and '@' not in username:
            return None
        if not password or (settings.AD_REQUIRE_HTTPS and (request is None or not request.is_secure())):
            raise PermissionDenied
        try:
            if settings.AD_AUTH_METHOD == 'windows':
                from .windows_password import windows_password_identity
                identity = windows_password_identity(username, password)
            else:
                identity = directory_identity(username, password)
        except PermissionDenied:
            raise
        except Exception as error:
            # Never log passwords, bind responses, connection repr or exception text.
            logger.warning('Active Directory authentication unavailable (%s)', type(error).__name__)
            raise PermissionDenied from None
        with transaction.atomic():
            user, created = get_user_model().objects.get_or_create(username=identity['username'])
            if not self.user_can_authenticate(user):
                raise PermissionDenied
            if created:
                user.set_unusable_password()
                user.is_staff = False
                user.is_superuser = False
            for field in ('first_name', 'last_name', 'email'):
                if field in identity:
                    setattr(user, field, identity[field])
            fields = [field for field in ('first_name', 'last_name', 'email') if field in identity]
            if created:
                fields += ['password', 'is_staff', 'is_superuser']
            if fields:
                user.save(update_fields=fields)
        # No AD password is saved. Portal roles are assigned by an administrator.
        return user
