from unittest.mock import patch,MagicMock
from pathlib import Path
from datetime import date
from django.test import SimpleTestCase,TestCase
from django.core.exceptions import ValidationError
from django.db import models
from .services.shareholder_write_guard import validate_private_writes
from .services.shareholder_sql import private_sql
from . import test_shareholders as fixtures


class PrivateWriteTests(SimpleTestCase):
    def test_native_updates_resolve_all_three_private_output_aliases(self):
        for alias,table in [('r','#share_report'),('f','#share_fact'),('a','#share_annual')]:
            for source in [f'UPDATE {alias} SET Amount=1 FROM {table} {alias};',
                           f'UPDATE [{alias}] SET Amount=1 FROM [{table}] AS [{alias}];',
                           f'UPDATE {alias} SET Amount=(SELECT MAX(x) FROM dbo.Rates x) FROM {table} {alias};']:
                validate_private_writes(source)

    def test_delete_and_join_aliases_still_target_local_rows_only(self):
        validate_private_writes('DELETE d FROM #docs d JOIN dbo.Rates r ON r.Id=d.Id;')
        validate_private_writes('UPDATE t SET x=1 FROM dbo.Rates r JOIN #rows t ON r.Id=t.Id;')
        validate_private_writes('UPDATE [#rows] SET x=1; INSERT #rows SELECT 1; DELETE FROM #rows;')

    def test_nested_select_cannot_disguise_permanent_update(self):
        for source in ['UPDATE r SET x=(SELECT MAX(x) FROM #rows r) FROM dbo.Real r;',
                       'UPDATE r SET x=1 FROM dbo.Real r; SELECT * FROM #rows r;',
                       'DELETE r FROM dbo.Real r WHERE EXISTS(SELECT 1 FROM #rows r);',
                       'UPDATE r SET x=1 FROM (SELECT * FROM #rows) r;']:
            with self.subTest(source=source),self.assertRaises(ValidationError):validate_private_writes(source)

    def test_permanent_global_and_unrecognized_write_targets_remain_blocked(self):
        for source in ['UPDATE dbo.Real SET x=1;', 'INSERT dbo.Real SELECT 1;',
                       'INSERT INTO dbo.Real SELECT 1;', 'DELETE FROM dbo.Real;',
                       'TRUNCATE TABLE dbo.Real;', 'CREATE TABLE dbo.Real(x int);',
                       'UPDATE "dbo"."Real" SET x=1;', 'UPDATE ##shared SET x=1;',
                       'UPDATE x SET a=1 FROM ##shared x;', 'UPDATE (SELECT 1) SET a=1;']:
            with self.subTest(source=source),self.assertRaises(ValidationError):validate_private_writes(source)

    def test_error_identifies_operation_and_target_without_dumping_sql(self):
        with self.assertRaises(ValidationError) as error:validate_private_writes("UPDATE dbo.Real SET Secret=N'private contents';")
        self.assertIn('UPDATE',str(error.exception));self.assertIn('dbo.Real',str(error.exception))
        self.assertNotIn('private contents',str(error.exception))

    def test_remapping_happens_before_alias_resolution(self):
        from .services.calculation_variants import isolate
        from .services.manager_correction import SQL
        original=fixtures.SQLTests().definition()
        correction=isolate(SQL)
        result=private_sql(original+';'+correction,'combined','aliases')
        self.assertIn('UPDATE r SET',result);self.assertIn('UPDATE f SET',result)
        self.assertIn('FROM #share_report r',result);self.assertIn('FROM #share_fact f',result)


class NativeCorrectionsTests(TestCase):
    def test_installed_pipeline_with_manager_and_annual_corrections_is_private(self):
        from .models import GlobalCoeffVersion,Subdivision,Chanel,SubdivisionManagerCoeff
        from .services import calculation_jobs
        version=GlobalCoeffVersion.objects.create(effective_from=date(2026,1,1))
        channel=Chanel.objects.create(chanel_name='alias corrections')
        branch=Subdivision.all_objects.create(subdivision_key='Д-Москва',subdivision_name='Москва',subdivision_global='Москва',chanel=channel)
        SubdivisionManagerCoeff.objects.create(version=version,subdivision=branch,coeff=1)
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=version.pk),status='approved')
        source=(Path(__file__).parent/'sql/review/sp_CalcBySub.refactored.sql').read_text()
        source=source[source.index('CREATE OR ALTER PROCEDURE'):].replace('@Year','@FactYear')
        source=source.replace("@PlanVersion nvarchar(255) = N'План продаж [Q1_2024] v4'","@PlanVersion nvarchar(255),@QuarterShare decimal(9,8),@YearShare decimal(9,8),@PayoutThreshold decimal(9,8)")
        for variant,mode in [('all_2026','combined'),('separate_2026','separate')]:
            cursor=MagicMock();cursor.fetchone.return_value=(source,None);cursor.fetchall.return_value=[('SubdivisionKey',),('SubdivisionName',)]
            with patch.object(calculation_jobs,'rules',return_value=[]):calculation_jobs.prepare(cursor,variant)
            result=private_sql(cursor.execute.call_args.args[0],mode,'aliases')
            for alias,table in [('r','#share_report'),('f','#share_fact'),('a','#share_annual')]:
                self.assertIn('UPDATE '+alias+' SET',result);self.assertIn('FROM '+table+' '+alias,result)
            self.assertNotIn('UPDATE dbo.',result)
