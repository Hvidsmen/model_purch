from time import perf_counter
from django.core.management.base import BaseCommand, CommandError
from admin_motivation.models import SalesPlanScenario
from admin_motivation.services.plan_summary_groups import ensure_summary_groups


class Command(BaseCommand):
    help = 'Prepare persistent plan-summary inputs without calculating motivation.'

    def add_arguments(self, parser):
        parser.add_argument('--plan', type=int, help='Scenario ID; otherwise all active plans.')
        parser.add_argument('--force', action='store_true', help='Rebuild even when current.')

    def handle(self, *args, **options):
        plans = SalesPlanScenario.objects.filter(is_deleted=False)
        if options['plan'] is not None:
            plans = plans.filter(pk=options['plan'])
            if not plans.exists():
                raise CommandError('План продаж не найден.')
        for plan in plans.iterator():
            started = perf_counter()
            self.stdout.write(f'План {plan.pk}: подготовка сводных данных…')
            groups = ensure_summary_groups(plan, force=options['force'])
            self.stdout.write(f'План {plan.pk}: групп {groups.count()}, {perf_counter() - started:.2f} с')
