from django.apps import AppConfig

class ModelPurchConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'model_purch'
    verbose_name = 'Model Purch'

    def ready(self):
        # Эта строка критически важна! Она регистрирует Dash-приложения при старте Django
        import model_purch.dash_apps  # noqa