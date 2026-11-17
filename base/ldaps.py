"""Password-free TLS preflight; verification is never disabled."""
import socket
import ssl
from urllib.parse import urlsplit


def check_tls(url,ca_file='',timeout=5):
    endpoint=urlsplit(url)
    if endpoint.scheme!='ldaps' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.path not in ('','/') or endpoint.query or endpoint.fragment:
        raise ValueError('Expected ldaps://hostname:636')
    context=ssl.create_default_context(cafile=ca_file or None)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    with socket.create_connection((endpoint.hostname,endpoint.port or 636),timeout=timeout) as raw:
        with context.wrap_socket(raw,server_hostname=endpoint.hostname) as secured:return secured.version()
