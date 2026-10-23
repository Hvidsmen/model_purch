from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from . import test_shared_versions
from .models import MotivationExportLog, MotivationExecution, MotivationCalculationSchedule, DirectorEmployee
from .services.workflow import context, export_snapshot
from .services.approval import approve_review, review_approval
from .services.sql_export import export_motivation
from .services.execution_tracking import observe


class WorkflowUxTests(TestCase):
    setUp = test_shared_versions.SharedMotivationVersionTests.setUp

    def approve(self):
        approve_review(review_approval(self.first)['token'])

    @patch('admin_motivation.services.calculation_jobs.prepare')
    def test_export_receipt_stales_when_exported_data_changes(self,prepare):
        self.approve()
        self.assertFalse(context()['export_current'])
        connection,cursor=Mock(),Mock()
        export_motivation(lambda *args:(connection,cursor))
        self.assertTrue(context()['export_current'])
        self.assertEqual(MotivationExportLog.objects.count(),1)
        self.good.brand='Updated brand';self.good.save()
        self.assertFalse(context()['export_current'])
        self.assertIn('Нужна выгрузка',context()['export_label'])

    def test_failed_export_never_creates_receipt(self):
        self.approve()
        connection,cursor=Mock(),Mock();cursor.execute.side_effect=RuntimeError('failure')
        with self.assertRaises(RuntimeError): export_motivation(lambda *args:(connection,cursor))
        self.assertFalse(MotivationExportLog.objects.exists())
        connection.rollback.assert_called_once()

    @patch('admin_motivation.services.calculation_jobs.prepare')
    def test_concurrent_change_cancels_remote_export(self,prepare):
        self.approve();connection,cursor=Mock(),Mock()
        with patch('admin_motivation.services.workflow.export_snapshot',side_effect=[{'fingerprint':'before'},{'fingerprint':'after'}]):
            with self.assertRaisesMessage(Exception,'изменились'): export_motivation(lambda *args:(connection,cursor))
        connection.commit.assert_not_called();connection.rollback.assert_called_once()
        self.assertFalse(MotivationExportLog.objects.exists())

    def test_execution_is_captured_from_actual_agent_record_and_becomes_stale(self):
        import json
        self.approve();snapshot=export_snapshot()
        MotivationExportLog.objects.create(fingerprint=snapshot['fingerprint'],snapshot=snapshot)
        job=MotivationCalculationSchedule.objects.create(name='Tracked',fact_year=2026,plan_version='Plan')
        connection,cursor=Mock(),Mock();run=uuid4()
        cursor.fetchone.return_value=(run,job.key,datetime(2026,1,1,10),datetime(2026,1,1,11),'completed',json.dumps({'fact_year':2026,'plan_version':'Plan'}),json.dumps(snapshot))
        execution=observe(job,lambda *args:(connection,cursor))
        self.assertEqual(execution.started_at.tzinfo,timezone.utc)
        self.assertTrue(context()['calculated_current'])
        self.good.brand='New';self.good.save()
        self.assertFalse(context()['calculated_current'])
        self.assertEqual(execution.parameters['plan_version'],'Plan')
        connection.close.assert_called_once()

    def test_missing_audit_does_not_invent_a_completed_run(self):
        job=MotivationCalculationSchedule.objects.create(name='Old')
        connection,cursor=Mock(),Mock();cursor.fetchone.return_value=None
        self.assertIsNone(observe(job,lambda *args:(connection,cursor)))
        self.assertFalse(MotivationExecution.objects.exists())

    def test_bulk_preview_does_not_mutate_and_changed_selection_is_rejected(self):
        self.first.refresh_from_db()
        data={'version':self.first.pk,'revision':self.first.revision,'goods':[self.good.pk],'variation':self.variation.pk,'kinds':['Политики'],'preview':'1'}
        url=reverse('motivation_bulk_formula')
        info=self.client.post(url,data).json()
        self.assertEqual(info['goods_count'],1)
        self.first.refresh_from_db();self.assertEqual(self.first.revision,int(data['revision']))
        response=self.client.post(url,{**data,'preview':'0','review_required':'yes','review_token':info['token'],'kinds':['Продажи']},follow=True)
        self.assertContains(response,'Выбор изменился после проверки')

    def test_failed_save_keeps_attempt_and_marks_invalid_field(self):
        response=self.client.post(reverse('gb_act'),{'version':self.first.pk,'action_button':'save_coeff',f'select_var_calc={self.good.pk}':self.variation.pk,f'global_coeff={self.global_coeff.pk}':'bad'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['coefficient_attempt']['fields'][f'global_coeff={self.global_coeff.pk}'],'bad')
        self.assertIn(f'global_coeff={self.global_coeff.pk}',response.context['coefficient_attempt']['errors'])
        self.global_coeff.refresh_from_db();self.assertEqual(self.global_coeff.motivation_coeff,.1)

    def test_archive_is_separate_read_only_view(self):
        director=DirectorEmployee.objects.create(full_name='History director',is_archived=True)
        url=reverse('motivation_director_directory')
        self.assertNotContains(self.client.get(url),director.full_name)
        response=self.client.get(url,{'archived':'1'})
        self.assertContains(response,director.full_name)
        self.assertContains(response,'Архив. Исторические коэффициенты сохранены.')
        self.assertNotContains(response,f'edit_employee={director.pk}')

    def test_report_receipt_renders_with_recorded_context(self):
        from django.utils import timezone
        MotivationExecution.objects.create(run_id=uuid4(),job_key=uuid4(),state='completed',started_at=timezone.now(),completed_at=timezone.now(),parameters={'fact_year':2026,'plan_version':'Recorded plan'},export_snapshot={'versions':[{'title':'Recorded coefficients','from':'2026-01-01'}]})
        with patch('admin_motivation.report_views.choices',return_value=[]),patch('admin_motivation.report_views.load_report',return_value={'currency':'USD','nodes':[],'periods':[]}):
            response=self.client.get(reverse('motivation_report',args=['manager-detail']),{'year':'2026','run':'1'})
        self.assertContains(response,'Recorded plan')

    def test_monitor_links_actual_record_and_refreshes_workflow(self):
        from django.utils import timezone
        self.approve();snapshot=export_snapshot()
        MotivationExportLog.objects.create(fingerprint=snapshot['fingerprint'],snapshot=snapshot)
        job=MotivationCalculationSchedule.objects.create(name='Linked',fact_year=2026,plan_version='Current plan')
        run=MotivationExecution.objects.create(run_id=uuid4(),job_key=job.key,state='completed',started_at=timezone.now(),completed_at=timezone.now(),parameters={'fact_year':2025,'plan_version':'Executed plan'},export_snapshot=snapshot)
        url=reverse('motivation_calculation_monitor',args=[job.pk])
        with patch('admin_motivation.operation_views.calculation_jobs.monitor',return_value={'status':'Завершено','history':[]}),patch('admin_motivation.services.execution_tracking.observe',return_value=run):
            data=self.client.get(url).json()
        self.assertIn('year=2025',data['report_url'])
        self.assertEqual(data['execution']['plan'],'Executed plan')
        self.assertTrue(data['workflow']['calculated_current'])
        with patch('admin_motivation.operation_views.calculation_jobs.monitor',return_value={'status':'Выполняется','history':[]}),patch('admin_motivation.services.execution_tracking.observe',return_value=run):
            data=self.client.get(url).json()
        self.assertIsNone(data['report_url'])
        self.assertFalse(data['workflow']['calculated_current'])
