import json
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from django.core.exceptions import ValidationError
from portal.trial_transfer import require_trial,clear_migration_seeds,reseed_identities,verify_data,compatibility_probe,set_phase
from portal.sqlserver_trial_config import redact as diagnostic_text


class Command(BaseCommand):
    help='Внутренние этапы изолированного SQLite → SQL Server теста.'
    def add_arguments(self,parser):
        parser.add_argument('stage',choices=('identity','clear-seeds','reseed','verify','probe'))
        parser.add_argument('--report')
    def handle(self,*args,**options):
        try:
            connection=require_trial()
            stage=options['stage']
            if stage=='identity':self.stdout.write('Идентичность тестовой базы подтверждена.');return
            if stage=='clear-seeds':clear_migration_seeds();self.stdout.write('Seed-данные новой тестовой базы очищены.');return
            if stage=='reseed':reseed_identities();self.stdout.write('IDENTITY выровнены по перенесённым ключам.');return
            report=verify_data() if stage=='verify' else compatibility_probe()
            if options['report']:
                path=Path(options['report']);path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            if not report['passed']:raise CommandError('Сверка/проверка не прошла. Смотрите отчёт; переключение рабочего портала запрещено.')
            if stage=='verify':set_phase(connection,'verified')
            self.stdout.write(self.style.SUCCESS('Сверка данных пройдена.' if stage=='verify' else 'Проверки SQL Server пройдены.'))
        except (ValidationError,CommandError) as error:raise CommandError(' '.join(error.messages) if isinstance(error,ValidationError) else str(error)) from None
        except Exception as error:
            if options.get('report'):Path(options['report']).write_text(json.dumps({'passed':False,'error':diagnostic_text(error)},ensure_ascii=False,indent=2),encoding='utf-8')
            raise CommandError(diagnostic_text(error)) from None
