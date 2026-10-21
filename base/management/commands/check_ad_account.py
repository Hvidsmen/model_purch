from django.core.management.base import BaseCommand, CommandError
from base.directory_search import search_accounts


class Command(BaseCommand):
    help = 'Проверить поиск точной доменной учётной записи без пароля и изменения прав.'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True, help='DOMAIN\\login, например TT\\akrasnoperov')

    def handle(self, *args, **options):
        try:
            rows = search_accounts(options['username'])
        except RuntimeError as error:
            raise CommandError(str(error)) from None
        for row in rows:
            self.stdout.write(f'{row["username"]} — {row["full_name"] or "ФИО не получено"}')
        if not rows:
            self.stdout.write('Windows не разрешила имя в учётную запись пользователя.')
        self.stdout.write('Пароль не запрашивался. Пользователи и права портала не изменены.')
