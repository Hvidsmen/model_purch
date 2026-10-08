from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SharedPurchaseMigrationTests(TransactionTestCase):
    def test_highest_id_survives_with_payments_all_originals_archived_and_reversible(self):
        old = [('model_purch', '0012_algorithmrun_parameters_algorithmrun_scenario_and_more')]
        new = [('model_purch', '0013_purchscenarioarchive_remove_purch_scenario_plan_and_more')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        Scenario = apps.get_model('model_purch', 'ScenarioModel')
        Purch = apps.get_model('model_purch', 'Purch')
        Pay = apps.get_model('model_purch', 'PurchPay')
        scenarios = [Scenario.objects.create(name=n, date_start_plan='2026-01-01', date_end_plan='2026-12-31') for n in ['A', 'B']]
        first = Purch.objects.create(name='Supplier', scenario_plan=scenarios[0], lag_income=10, lage_make=1)
        last = Purch.objects.create(name=' supplier ', scenario_plan=scenarios[1], lag_income=77, lage_make=8)
        unassigned = Purch.objects.create(name='Other', lag_income=50, lage_make=0)
        for purchase in [first, last, unassigned]:
            Pay.objects.create(purch=purchase, name=f'Payment {purchase.pk}', percent_pay=100, lag_day_pay=purchase.pk)
        original_purchases = list(Purch.objects.order_by('pk').values())
        original_payments = list(Pay.objects.order_by('pk').values())
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        apps = executor.loader.project_state(new).apps
        Purch = apps.get_model('model_purch', 'Purch')
        Pay = apps.get_model('model_purch', 'PurchPay')
        Archive = apps.get_model('model_purch', 'PurchScenarioArchive')
        self.assertEqual(set(Purch.objects.values_list('pk', flat=True)), {last.pk, unassigned.pk})
        self.assertEqual(Purch.objects.get(pk=last.pk).lag_income, 77)
        self.assertEqual(Purch.objects.get(pk=last.pk).name, 'supplier')
        self.assertEqual(Pay.objects.get(purch_id=last.pk).name, f'Payment {last.pk}')
        for original in original_purchases:
            archive = Archive.objects.get(original_id=original['id'])
            self.assertEqual(archive.original_data, original)
            self.assertEqual(archive.payments, [p for p in original_payments if p['purch_id'] == original['id']])
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        self.assertEqual(list(apps.get_model('model_purch', 'Purch').objects.order_by('pk').values()), original_purchases)
        self.assertEqual(list(apps.get_model('model_purch', 'PurchPay').objects.order_by('pk').values()), original_payments)
        connection.check_constraints()
