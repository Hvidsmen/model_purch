import json
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from model_purch.services.product_duty_repair import read_targets, repair_targets


class Command(BaseCommand):
    help='Адресно исправить товары из отчёта пошлин; предварительный пересчёт, транзакция, без резервных копий.'

    def add_arguments(self, parser):
        parser.add_argument('--audit',required=True)
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--expected-preview')
        parser.add_argument('--json',action='store_true',dest='json_output')

    def handle(self, **options):
        try:
            targets,archived,other=read_targets(options['audit'])
            result=repair_targets(targets,apply=options['apply'],expected_preview=options['expected_preview'])
            result.update(archived_skipped=archived,other_skipped=other)
        except (ValidationError,OSError,UnicodeError) as exc:
            raise CommandError(str(exc)) from exc
        if options['json_output']:
            self.stdout.write(json.dumps(result,ensure_ascii=False,allow_nan=False))
            return
        for row in result['rows']:
            self.stdout.write(f"#{row['id']} · сценарий {row['scenario_id']} · {row['product']}: ставка {row['old_rate']} → {row['new_rate']}; DDP {row['ddp_before']:.2f} → {row['ddp_after']:.2f}")
        self.stdout.write(f"Выбрано: {result['selected']}; изменений: {result['changed']}; уже правильные: {len(result['already_correct'])}. Архивных строк пропущено: {archived}; прочих строк: {other}.")
        self.stdout.write('preview_hash: '+result['preview_hash'])
        self.stdout.write('Исправлено транзакционно. Резервные копии не создавались.' if result['applied'] else 'Предварительный пересчёт. Данные не изменены. Резервные копии не создавались.')
