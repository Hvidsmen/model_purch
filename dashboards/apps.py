from django.apps import AppConfig


class DashboardsConfig(AppConfig):
    name = 'dashboards'
    verbose_name = 'Дашборды'

    def ready(self):
        import os
        import sys
        import threading
        if ('runserver' in sys.argv and ('--noreload' in sys.argv or os.environ.get('RUN_MAIN') == 'true')) or os.environ.get('DASHBOARDS_SCHEDULER') == '1':
            from .services.snapshots import scheduler_loop
            threading.Thread(target=scheduler_loop, args=(threading.Event(),), daemon=True, name='dealer-daily-scheduler').start()
