from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class GoodsIdentityMigrationTests(TransactionTestCase):
    def test_highest_id_kept_and_all_conflicting_attributes_archived(self):
        old = [('model_purch', '0009_kindlagpay_purch_lage_make_purchpay_kind_lag_pay')]
        new = [('model_purch', '0010_pggoodsduplicatearchive_pggoods_planning_group_key_and_more')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        Goods = apps.get_model('model_purch', 'PGGoods')
        Scenario = apps.get_model('model_purch', 'ScenarioModel')
        kind = apps.get_model('model_purch', 'KindPurch').objects.create(name='Purchased')
        first = Scenario.objects.create(name='Plan', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        second = Scenario.objects.create(name='Other', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        values = dict(planning_group='ВЕНТ Канальная', planning_sales='Sales', group_goods='Group', brand='Brand',
                      purch='Supplier', kind_purch_id=kind.pk, volume=1, exw_usd=2, ddp_usd=3, kddp=1.5,
                      stock_cnt_day=30, percent_stock_end=20, scenario_plan_id=first.pk)
        oldest = Goods.objects.create(**values)
        values.update(planning_group=' вент канальная ', planning_sales='New sales', ddp_usd=99, stock_cnt_day=77)
        newest = Goods.objects.create(**values)
        values['scenario_plan_id'] = second.pk
        separate = Goods.objects.create(**values)
        before = list(Goods.objects.order_by('id').values())
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        apps = executor.loader.project_state(new).apps
        Goods = apps.get_model('model_purch', 'PGGoods')
        Archive = apps.get_model('model_purch', 'PGGoodsDuplicateArchive')
        self.assertEqual(set(Goods.objects.values_list('pk', flat=True)), {newest.pk, separate.pk})
        self.assertEqual(Goods.objects.get(pk=newest.pk).ddp_usd, 99)
        self.assertEqual(Goods.objects.get(pk=newest.pk).stock_cnt_day, 77)
        self.assertEqual(Goods.objects.get(pk=newest.pk).planning_group_key, Goods.objects.get(pk=separate.pk).planning_group_key)
        for snapshot in before[:2]:
            archive = Archive.objects.get(original_id=snapshot['id'])
            self.assertEqual(archive.kept_id, newest.pk)
            self.assertEqual({key: archive.original_data[key] for key in snapshot}, snapshot)
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        Goods = apps.get_model('model_purch', 'PGGoods')
        self.assertEqual(list(Goods.objects.order_by('id').values()), before)
        connection.check_constraints()
