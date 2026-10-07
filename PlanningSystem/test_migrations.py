from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SubdivisionHeaderMigrationTests(TransactionTestCase):
    def test_existing_rows_survive_forward_and_reverse_migration(self):
        old_target = [('PlanningSystem', '0013_percentsubdivisionseason')]
        new_target = [('PlanningSystem', '0014_alter_pecentsubdivisiongoods_header')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(old_target)
        apps = executor.loader.project_state(old_target).apps
        model = lambda name: apps.get_model('PlanningSystem', name)
        option = model('OptionPlanRef').objects.create(option_key='plan', option_name='Plan')
        channel = model('ChanelRef').objects.create(chanel_key='channel', chanel_name='Channel')
        goods = model('GoodsRef').objects.create(goods_key='goods')
        branch = model('SubdivisionRef').objects.create(subdivision_key='branch', subdivision_name='Branch')
        # Pre-existing percentage header ID 1 makes new header IDs overlap source IDs.
        model('PercentSubdivisionHeader').objects.create(id=1, opition_plan_id=option.pk, year=2024)
        row_ids = []
        for pk, year in [(1, 2025), (2, 2026)]:
            header = model('PlanSalesByChanelHeader').objects.create(
                id=pk, opition_plan_id=option.pk, year=year, sheet_name=f'Year {year}', file=f'plan-{year}.xlsx',
            )
            row = model('PecentSubdivisionGoods').objects.create(
                header_id=header.pk, chanel_id=channel.pk, goods_id=goods.pk,
                subdivision_id=branch.pk, percent=pk * 10,
            )
            row_ids.append(row.pk)
        executor = MigrationExecutor(connection)
        executor.migrate(new_target)
        new_apps = executor.loader.project_state(new_target).apps
        rows = new_apps.get_model('PlanningSystem', 'PecentSubdivisionGoods')
        for pk, year, percent in zip(row_ids, [2025, 2026], [10, 20]):
            row = rows.objects.select_related('header').get(pk=pk)
            self.assertEqual(row.header.year, year)
            self.assertEqual(row.header.file.name, f'plan-{year}.xlsx')
            self.assertEqual(row.percent, percent)
        connection.check_constraints()
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        old_apps = executor.loader.project_state(old_target).apps
        rows = old_apps.get_model('PlanningSystem', 'PecentSubdivisionGoods')
        for pk, year in zip(row_ids, [2025, 2026]):
            self.assertEqual(rows.objects.select_related('header').get(pk=pk).header.year, year)
        connection.check_constraints()
