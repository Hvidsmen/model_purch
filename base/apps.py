from django.apps import AppConfig


class BaseConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'base'

    def ready(self):
        from django.db.models.signals import post_migrate
        from .access import ensure_roles
        post_migrate.connect(ensure_roles, dispatch_uid="base.portal_roles")
