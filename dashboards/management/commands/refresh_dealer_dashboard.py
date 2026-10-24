from django.core.management.base import BaseCommand, CommandError
from dashboards.services.snapshots import run_refresh


class Command(BaseCommand):
    help = 'Однократно обновить постоянный снимок дилерских продаж из DataWH.'

    def handle(self, **options):
        if not run_refresh():
            raise CommandError('Обновление не выполнено: уже идёт другой запуск или произошла ошибка DataWH. Проверьте журнал.')
        self.stdout.write(self.style.SUCCESS('Снимок дилерских продаж обновлён.'))
