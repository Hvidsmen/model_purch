from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class WeightPolicyMigrationTests(TransactionTestCase):
    def test_existing_and_archived_exempt_but_new_checked(self):
        before = ('model_purch', '0031_pggoods_weight_kg')
        after = ('model_purch', '0032_scenariomodel_check_weight_completeness')
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        try:
            apps = executor.loader.project_state([before]).apps
            Scenario = apps.get_model('model_purch', 'ScenarioModel')
            active = Scenario.objects.create(name='Old', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
            archived = Scenario.objects.create(name='Archived', date_start_plan='2025-01-01', date_end_plan='2025-12-31', archived_at=timezone.now())
            executor = MigrationExecutor(connection)
            executor.migrate([after])
            Scenario = executor.loader.project_state([after]).apps.get_model('model_purch', 'ScenarioModel')
            self.assertFalse(Scenario._base_manager.get(pk=active.pk).check_weight_completeness)
            self.assertFalse(Scenario._base_manager.get(pk=archived.pk).check_weight_completeness)
            new = Scenario.objects.create(name='New', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
            self.assertTrue(new.check_weight_completeness)
        finally:
            MigrationExecutor(connection).migrate([after])
