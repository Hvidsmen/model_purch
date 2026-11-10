from pathlib import Path
from unittest.mock import patch, MagicMock
from django.core.exceptions import ValidationError
from django.urls import reverse
from . import test_recalculation_2025 as helpers
from django.test import TestCase
from .models import MotivationCalculationSchedule, Motivation2025Input
from .services.recalculation_2025_sql import prepare, template_source, PROC


class SavedRecalculation2025Tests(TestCase):
    setUp = helpers.Recalculation2025WebTests.setUp
    make_job = helpers.Recalculation2025WebTests.make_job

    def test_failed_start_keeps_snapshot_and_does_not_validate_upload_form(self):
        job=self.make_job();saved=job.recalculation_2025
        with patch('admin_motivation.services.calculation_jobs.monitor',return_value={}), patch(
                'admin_motivation.services.calculation_jobs.start_job',side_effect=ValidationError('Не удалось подготовить SQL')):
            response=self.client.post(self.url,{'action':'run','job':job.pk},follow=True)
        job.refresh_from_db()
        self.assertEqual(job.error,'Не удалось подготовить SQL')
        self.assertFalse(job.synced)
        self.assertFalse(response.context['form'].is_bound)
        self.assertFalse(response.context['form'].errors)
        self.assertEqual(response.context['form'].initial['plan_version'],'Plan 25')
        self.assertEqual(response.context['state'],'start_failed')
        self.assertContains(response,'Входные данные сохранены. Запуск не выполнен')
        self.assertContains(response,'Не удалось подготовить SQL')
        self.assertNotContains(response,'This field is required')
        self.assertEqual(Motivation2025Input.objects.count(),1)
        self.assertEqual(job.recalculation_2025.source_sha256,saved.source_sha256)
        reloaded=self.client.get(response.redirect_chain[-1][0])
        self.assertContains(reloaded,'Не удалось подготовить SQL')
        self.assertEqual(reloaded.context['job'].pk,job.pk)

    def test_existing_coefficients_can_be_reused_without_another_upload(self):
        old=self.make_job()
        response=self.client.post(self.url,dict(action='upload',plan_version='Plan 25',quarter_share=70,year_share=30,payout_threshold='.9'),follow=True)
        new=response.context['job']
        self.assertNotEqual(new.pk,old.pk)
        self.assertEqual(new.recalculation_2025.coefficients,old.recalculation_2025.coefficients)
        self.assertEqual(new.recalculation_2025.source_sha256,old.recalculation_2025.source_sha256)
        self.assertEqual(new.quarter_share,70)
        self.assertEqual(old.quarter_share,80)
        self.assertEqual(response.context['form'].initial['payout_threshold'],new.payout_threshold)

    def test_retry_clears_the_persisted_start_error(self):
        job=self.make_job();job.error='Previous failure';job.save(update_fields=['error'])
        with patch('admin_motivation.services.calculation_jobs.monitor',return_value={}), patch(
                'admin_motivation.services.calculation_jobs.start_job'), patch(
                'admin_motivation.services.execution_tracking.observe',return_value=None):
            response=self.client.post(self.url,{'action':'run','job':job.pk},follow=True)
        job.refresh_from_db();self.assertEqual(job.error,'');self.assertTrue(job.synced)
        self.assertFalse(response.context['form'].errors)
        self.assertEqual(Motivation2025Input.objects.count(),1)

    def test_refresh_and_navigation_stay_on_the_selected_snapshot(self):
        selected=self.make_job();newest=self.make_job()
        response=self.client.get(self.url+f'?job={selected.pk}')
        self.assertEqual(response.context['job'].pk,selected.pk)
        self.assertNotEqual(selected.pk,newest.pk)
        self.assertContains(response,f'?job={selected.pk}&amp;export=xlsx')
        self.assertEqual(self.client.get(self.url+'?job=bad').status_code,404)


from django.test import SimpleTestCase


class Native2025PreparationTests(SimpleTestCase):
    def test_prepare_does_not_read_or_depend_on_live_procedure_shape(self):
        cursor=MagicMock();cursor.fetchone.side_effect=[(None,),(0,)]
        prepare(cursor)
        statements=[c.args[0] for c in cursor.execute.call_args_list]
        self.assertFalse(any('OBJECT_DEFINITION' in q for q in statements))
        sql=statements[-1]
        self.assertIn('CREATE PROCEDURE '+PROC,sql)
        self.assertNotIn('#director_employees',sql)
        self.assertNotIn('PortalSubdivisionMerge',sql)
        self.assertNotIn('WHERE 1=0 AND',sql)
        self.assertNotIn('{{PROCEDURE}}',sql)
        self.assertNotRegex(sql,r'(?im)^\s*GO\s*$')
        self.assertNotRegex(sql,r'(?i)\bUPDATE\s+DataWH\.dbo\.Sales\b')
        self.assertIn('FROM #portal_motivation_sales',sql)
        self.assertIn('@QuarterShare',sql)
        self.assertIn('@YearShare',sql)
        self.assertIn('@PayoutThreshold',sql)
        self.assertIn('Price - AmountUSDIt_ITW + BallsOuterUSD',sql)
        self.assertIn('PlanFactTest2025',sql)

    def test_existing_test_procedure_is_altered_without_touching_main(self):
        cursor=MagicMock();cursor.fetchone.side_effect=[(123,),(0,)]
        prepare(cursor)
        sql=cursor.execute.call_args.args[0]
        self.assertIn('ALTER PROCEDURE '+PROC,sql)
        self.assertNotIn('ALTER PROCEDURE [motivation].[sp_CalcBySub]',sql)

    def test_native_template_has_fixed_year_and_q1_inputs(self):
        sql=template_source()
        self.assertIn('@FactYear int = 2025',sql)
        self.assertNotIn('@Year int',sql)
        self.assertNotIn('2024',sql)
        self.assertNotIn('2026',sql)
        self.assertIn("СуммаОтгрузокUSD СуммаОтгрузокUSD\n\t,СуммаОтгрузокUSD СуммаПланUSD\n\t,'1 Квартал'",sql)

    def test_native_preparation_supports_dated_global_formula(self):
        cursor=MagicMock();cursor.fetchone.side_effect=[(None,),(1,)]
        prepare(cursor)
        sql=cursor.execute.call_args.args[0]
        self.assertIn("WHEN VariationCalculate = N'Подразделение(Глобально)'",sql)
        self.assertIn('@PayoutThreshold',sql)
        self.assertNotIn('#director_employees',sql)
        self.assertIn('old.VariationCalculate',sql)

    def test_prepared_procedure_avoids_openjson_at_old_compatibility_levels(self):
        cursor=MagicMock();cursor.fetchone.side_effect=[(None,),(0,)]
        prepare(cursor)
        sql=cursor.execute.call_args.args[0]
        self.assertNotIn('OPENJSON',sql.upper())
        self.assertIn("@Test2025Xml.nodes('/coefficients/coefficient')",sql)
        self.assertIn("r.record.value('(k4/text())[1]','decimal(12,6)')",sql)
        self.assertIn("COALESCE(r.record.value('(brand/text())[1]','nvarchar(255)'),N'') AS Brand",sql)
        self.assertRegex(sql,r';\s*WITH fact AS')

    def test_xml_creation_options_are_set_in_a_separate_batch(self):
        cursor=MagicMock();cursor.fetchone.side_effect=[(None,),(0,)]
        prepare(cursor)
        statements=[call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(statements[-2],'SET QUOTED_IDENTIFIER ON; SET ANSI_NULLS ON;')
        self.assertIn('CREATE PROCEDURE '+PROC,statements[-1])
        self.assertNotIn('SET QUOTED_IDENTIFIER',statements[-1])

    def test_sql_319_has_a_safe_readable_message(self):
        import pyodbc
        from .services.calculation_jobs import JobOperationError,failure_message
        exc=JobOperationError('Подготовка расчётной процедуры',pyodbc.ProgrammingError('42000','Private SQL text with secret (319)'))
        message=failure_message(exc,'test-reference')
        self.assertIn('WITH',message)
        self.assertIn('SQL 319',message)
        self.assertNotIn('secret',message)
