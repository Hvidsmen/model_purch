"""
Middleware для установки SECRET_KEY во Flask-сервер Dash-приложений.
"""

from django.conf import settings


class DashSecretKeyMiddleware:
    """
    Middleware, который устанавливает SECRET_KEY из Django настроек
    во внутренний Flask-сервер Dash при каждом запросе.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Проверяем, является ли запрос к Dash-приложению
        if hasattr(request, 'path') and '/django_plotly_dash/' in request.path:
            # Импортируем реестр Dash-приложений
            try:
                from django_plotly_dash import DjangoDash
                # Получаем все зарегистрированные приложения
                # и устанавливаем им secret_key
                for app_name in DjangoDash.app_names:
                    try:
                        app = DjangoDash(app_name)
                        # Получаем реальный экземпляр Dash
                        dash_instance = app.as_dash_instance()
                        # Устанавливаем SECRET_KEY
                        dash_instance.server.secret_key = settings.SECRET_KEY
                    except Exception:
                        pass  # Игнорируем ошибки для отдельных приложений
            except Exception:
                pass  # Игнорируем ошибки импорта

        response = self.get_response(request)
        return response