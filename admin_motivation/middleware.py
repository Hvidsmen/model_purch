from .services.subdivision_scope import exclusion_scope


class MotivationExclusionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # One fresh snapshot per authenticated request, shared by all nested helpers.
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return self.get_response(request)
        with exclusion_scope():
            return self.get_response(request)
