from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from . import test_director_scenarios_end_to_end as fixtures
from .models import DirectorScenarioComparison
from .services.director_scenario_progress import Progress,token_for


class DirectorProgressTests(fixtures.ScenarioFlow,TestCase):
    def setUp(self):
        self.setup_flow()
        self.root=TemporaryDirectory();self.addCleanup(self.root.cleanup)
        mocked=patch('admin_motivation.services.director_scenario_progress.tempfile.gettempdir',return_value=self.root.name)
        mocked.start();self.addCleanup(mocked.stop)
        self.user=User.objects.get(username='portal-test-admin')

    def test_json_run_records_real_stages_and_duplicate_does_not_recalculate(self):
        token=token_for(self.user);stages=[];original=Progress.update
        def track(progress,stage,*args,**kwargs):
            stages.append(stage);return original(progress,stage,*args,**kwargs)
        with patch('admin_motivation.services.director_scenario_inputs.connect_database',self.warehouse.connector),patch.object(Progress,'update',track):
            response=self.client.post(self.url,{**self.payload,'progress_token':token},HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code,200)
        self.assertIn('calculation=',response.json()['url'])
        for case in range(1,6):self.assertIn(f'Расчёт варианта {case} из 5',stages)
        self.assertIn('Чтение подготовленного факта 2026',stages)
        calls=len(self.warehouse.calls)
        status=self.client.get(self.url,{'progress':token})
        self.assertEqual(status.json()['state'],'completed')
        self.assertEqual(status.json()['step'],12)
        self.assertGreaterEqual(status.json()['elapsed'],0)
        self.assertEqual(status.json()['url'],response.json()['url'])
        self.assertEqual(status.headers['Cache-Control'],'no-store')
        self.assertEqual(len(self.warehouse.calls),calls)
        duplicate=self.client.post(self.url,{**self.payload,'progress_token':token},HTTP_ACCEPT='application/json')
        self.assertEqual(duplicate.status_code,409)
        self.assertEqual(DirectorScenarioComparison.objects.count(),1)

    def test_invalid_form_finishes_with_error_and_issues_fresh_token(self):
        token=token_for(self.user)
        response=self.client.post(self.url,{'progress_token':token},HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code,400)
        self.assertIn('Обязательное',response.json()['error'])
        self.assertNotEqual(response.json()['progress_token'],token)
        self.assertEqual(self.client.get(self.url,{'progress':token}).json()['state'],'failed')

    def test_status_is_scoped_to_user_and_malformed_tokens_are_rejected(self):
        stranger=User.objects.create(username='Other')
        self.assertEqual(self.client.get(self.url,{'progress':token_for(stranger)}).status_code,403)
        self.assertEqual(self.client.get(self.url,{'progress':'broken'}).status_code,400)
        token=token_for(self.user)
        self.assertEqual(self.client.get(self.url,{'progress':token}).json()['state'],'pending')
