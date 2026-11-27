from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from model_purch.models import GoodsGroup
from model_purch.services.duty_repair import repair


class Command(BaseCommand):
    help='Проверить подозрительные пошлины; исправить одну группу только по явным исходной и новой ставкам.'

    def add_arguments(self, parser):
        parser.add_argument('--group')
        parser.add_argument('--from-rate')
        parser.add_argument('--to-percent')
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--backup-file')

    def handle(self, **options):
        if not options['group']:
            if options['apply']:
                raise CommandError('Для исправления укажите --group, --from-rate и --to-percent.')
            self.stdout.write('Проверка без изменений. Ставки выше 100% требуют проверки, но не обязательно ошибочны:')
            for group in GoodsGroup.objects.filter(duty_rate__gt=1).order_by('name'):
                self.stdout.write(f'{group.name}: сохранено {group.duty_rate}; отображается {group.duty_rate*100:g}%')
            return
        if options['from_rate'] is None or options['to_percent'] is None:
            raise CommandError('Укажите --from-rate (хранимый коэффициент) и --to-percent (правильный процент).')
        try:
            result=repair(options['group'],options['from_rate'],options['to_percent'],apply=options['apply'],backup_file=options['backup_file'])
        except (ValidationError,GoodsGroup.DoesNotExist,OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"{result['group']}: {result['old_rate']} → {result['new_rate']} ({result['new_percent']}%).")
        for row in result['goods']:
            self.stdout.write(f"#{row['id']} · сценарий {row['scenario_id']} · {row['planning_group']}: DDP {row['before']['ddp_usd']:.2f} → {row['after']['ddp_usd']:.2f}")
        self.stdout.write(f"Товаров: {len(result['goods'])}. Индивидуальных ставок сохранено: {result['preserved_individual']}. Архивы не изменены.")
        self.stdout.write('Исправлено. Резервная копия: '+options['backup_file'] if result['applied'] else 'Предварительный просмотр. Данные не изменены.')
