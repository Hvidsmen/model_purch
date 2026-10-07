from django.apps import AppConfig

class ModelPurchConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'model_purch'
    verbose_name = 'Model Purch'

    def ready(self):
        from django.conf import settings

        # The routed stock chart uses JSON + Plotly, not the legacy Dash app.
        if getattr(settings, 'ENABLE_LEGACY_DASH', False):
            import model_purch.dash_apps  # noqa: F401
