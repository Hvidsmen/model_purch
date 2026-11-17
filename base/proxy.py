"""Trust forwarded headers only on connections from the configured HAProxy."""
from ipaddress import ip_address,ip_network
from django.conf import settings

FORWARDED=('HTTP_FORWARDED','HTTP_X_FORWARDED_FOR','HTTP_X_FORWARDED_HOST','HTTP_X_FORWARDED_PROTO','HTTP_X_FORWARDED_PORT')


class TrustedProxyMiddleware:
    def __init__(self,get_response):
        self.get_response=get_response
        self.networks=tuple(ip_network(value) for value in settings.PORTAL_TRUSTED_PROXIES)

    def __call__(self,request):
        try:
            peer=ip_address(request.META.get('REMOTE_ADDR',''))
            trusted=any(peer in network for network in self.networks)
        except ValueError:trusted=False
        if not trusted:
            for name in FORWARDED:request.META.pop(name,None)
        # REMOTE_ADDR remains the transport peer, never a user-supplied XFF.
        return self.get_response(request)
