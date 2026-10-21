from django.core.management.base import BaseCommand, CommandError
from base.windows_domains import domain_map


class Command(BaseCommand):
    help = 'Показать домены AD и доверенные домены Windows, доступные порталу.'

    def handle(self, *args, **options):
        try:
            domains = domain_map()
        except Exception as error:
            raise CommandError(f'Не удалось получить домены Windows: {error}') from None
        self.stdout.write('NetBIOS — DNS')
        for name, dns in sorted(domains.items()):
            self.stdout.write(f'{name.upper()} — {dns or "DNS-имя не возвращено Windows"}')
        self.stdout.write(f'Всего доменов: {len(domains)}. Учётные записи и права портала не изменены.')
