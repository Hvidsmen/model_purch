from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch,Mock
from django.test import TestCase,SimpleTestCase
from django.http import QueryDict
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from openpyxl import load_workbook
from base.testing import authorize_admin
from .models import MotivationCalculationSchedule,ShareholderCalculation,ShareholderSnapshot
from .services.shareholder_allocation import author_weights,split_amount
from .services import shareholder_reports as reports,shareholder_runner as runner
from .services.shareholder_sql import private_sql
from .services.prepared_sales import MARKER
from .test_manager_detail import detail_rows


class AllocationTests(SimpleTestCase):
    def test_small_authors_are_split_by_retained_turnover_in_each_physical_branch(self):
        rows=author_weights([('Москва','A',600),('Москва','B',395),('Москва','C',5),('ТТ','D',999),('ТТ','C',1)],1)
        small=[r for r in rows if r['branch']=='Москва' and r['original']=='C']
        self.assertEqual({r['assigned'] for r in small},{'A','B'})
        self.assertEqual(sum(r['weight'] for r in small),1)
        self.assertEqual([r['assigned'] for r in rows if r['branch']=='ТТ' and r['original']=='C'],['D'])
        for amount in ('12345.678901','-40.99','0'):
            self.assertEqual(sum(split_amount(amount,small)),Decimal(amount))
        self.assertEqual([r['weight'] for r in rows if r['original']=='A'],[1])

    def test_exact_threshold_retained_and_negative_returns_redistributed(self):
        rows=author_weights([('A','main',990),('A','exact',10),('A','return',-1),('A',None,1)],1)
        self.assertEqual([r['assigned'] for r in rows if r['original']=='exact'],['exact'])
        self.assertNotIn('return',{r['assigned'] for r in rows})
        self.assertEqual(sum(r['weight'] for r in rows if r['original']==''),1)

    def test_invalid_or_unallocatable_totals_fail_instead_of_inventing_weights(self):
        for source,threshold in [([('A','x',1),('A','y',1)],60),([('A','x',-10)],1),([('A','x','NaN')],1),([('A','x',1)],100)]:
            with self.assertRaises(ValidationError):author_weights(source,threshold)
        self.assertEqual(author_weights([('A','x',0)],1),[])


class SQLTests(SimpleTestCase):
    def definition(self):
        return '''CREATE OR ALTER PROCEDURE [motivation].[PortalCalcAll2026]
 @FactYear int,@PlanVersion nvarchar(500),@QuarterShare decimal(9,8),@YearShare decimal(9,8),@PayoutThreshold decimal(9,8) AS
 BEGIN
 '''+MARKER+'''
 DECLARE @PortalPreparedRun uniqueidentifier;
 SELECT * INTO #portal_motivation_sales FROM DataWH.motivation.PortalPreparedSales WHERE ClosedDealDate>='20260101';
 ;WITH fact AS (SELECT * FROM #portal_motivation_sales)
 SELECT * INTO DataWH.motivation.FactAll2026 FROM fact;
 IF OBJECT_ID('DataWH.motivation.PlanFactAll2026') IS NOT NULL DROP TABLE DataWH.motivation.PlanFactAll2026;
 SELECT * INTO DataWH.motivation.PlanFactAll2026 FROM DataWH.motivation.FactAll2026;
 SELECT * INTO DataWH.motivation.ReportMotivationByDealAuthorAll2026 FROM DataWH.motivation.PlanFactAll2026;
 SELECT * INTO DataWH.motivation.AnnualAccrualAll2026 FROM DataWH.motivation.ReportMotivationByDealAuthorAll2026;
 END'''

    def test_cutoff_and_all_four_output_tables_are_private_with_bracketed_header(self):
        result=private_sql(self.definition(),'combined','test')
        self.assertIn('ClosedDealDate<@ShareCutoff',result)
        self.assertIn("OBJECT_ID(N'tempdb..#share_plan_fact')",result)
        self.assertNotIn('DataWH.motivation.FactAll2026',result)
        self.assertNotIn('CREATE OR ALTER PROCEDURE',result)
        self.assertLess(result.index('INTO #share_transformed'),result.index(';WITH fact'))
        self.assertIn('WITH fact AS (SELECT * FROM #share_transformed)',result)
        self.assertIn('ShareOriginalAuthor',result)
        self.assertIn('не сохранило оборот',result)

    def test_unexpected_persistent_write_or_procedure_call_is_rejected(self):
        for statement in ('INSERT INTO dbo.Sales SELECT 1;','UPDATE dbo.Sales SET Price=0;','CREATE TABLE dbo.Unexpected(id int);','EXEC dbo.MutateSales;','MERGE dbo.Sales USING x ON 1=1;'):
            with self.assertRaises(ValidationError):private_sql(self.definition()+';'+statement,'combined','test')

    def test_incompatible_procedures_fail_closed(self):
        for source in (None,'SELECT 1',self.definition().replace('FROM DataWH.motivation.PortalPreparedSales','FROM something'),self.definition().replace(';WITH fact AS',';WITH changed AS')):
            with self.assertRaises(ValidationError):private_sql(source,'combined','test')


class SnapshotTests(TestCase):
    def setUp(self):
        self.user=authorize_admin(self.client)
        self.job=MotivationCalculationSchedule.objects.create(name='All 2026',variant='all_2026',fact_year=2026,plan_version='Plan',quarter_share=80,year_share=20)
        self.run=ShareholderCalculation.objects.create(name='Shareholders',as_of=date(2026,11,20),scenario='combined',quarters=[1,2,3,4],source_job=self.job,
            created_by=self.user,state='completed',stage='Готово',configuration={'plan_version':'Plan','quarter_share':'80','year_share':'20','payout_threshold':'.8'})
        source=[dict(row,Подразделение='Д-Москва') for row in detail_rows()]
        ShareholderSnapshot.objects.create(sequence=0,calculation=self.run,mode='combined',dataset='report_pol',payload=runner.serial(source))
        ShareholderSnapshot.objects.create(sequence=0,calculation=self.run,mode='combined',dataset='annual_pol',payload=[{'Подразделение':'Д-Москва','АвторСделки':'Иванов','Квартал':1,'AnnualUSD':'25','AnnualRUB':'2500'}])

    def url(self,kind):return reverse('motivation_shareholder_report',args=[self.run.pk,kind])

    def test_five_reports_and_excel_agree_and_never_read_live_sql(self):
        with patch('admin_motivation.conns.connect_database',side_effect=AssertionError('No live SQL')):
            totals=[]
            for kind in reports.TITLES:
                if kind=='deals':continue
                response=self.client.get(self.url(kind),{'type':'Политики','author':'Иванов','period':['1','5']})
                self.assertEqual(response.status_code,200)
                totals.append(response.context['ledger']['total'])
                self.assertContains(response,'20.11.2026')
                export=self.client.get(self.url(kind),{'type':'Политики','author':'Иванов','period':['1','5'],'export':'xlsx'})
                self.assertEqual(export.status_code,200)
                book=load_workbook(BytesIO(b''.join(export.streaming_content)));self.assertGreater(len(book.sheetnames),1);book.close();export.close()
            self.assertEqual(totals,[Decimal(180)]*5)

    def test_closed_deals_uses_snapshot_filters_totals_and_excel(self):
        deal={'ClosedDate':'2026-04-05','SourceBranch':'Д-Москва','SourceRow':'1','DocumentNumber':'DOC-1','ProductCode':'ERP-1',
              'OriginalAuthor':'Исходный автор','AssignedAuthor':'Иванов','Weight_':'0.25','AllocatedUSD':'25.50','AllocatedRUB':'2550',
              'ГруппаПлановПродаж':'RAC','ГруппаТоваров':'AC','Марка':'Brand'}
        ShareholderSnapshot.objects.create(calculation=self.run,dataset='deals',mode='combined',sequence=0,payload=[deal])
        with patch('admin_motivation.services.shareholder_reports.data_for',side_effect=AssertionError('Deals must not rebuild premiums')):
            response=self.client.get(self.url('deals'),{'period':'2','author':'Иванов'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.context['deals_usd'],Decimal('25.50'))
            self.assertEqual(response.context['deals_rub'],Decimal('2550'))
            self.assertContains(response,'DOC-1')
            self.assertContains(response,'Исходный автор')
            self.assertNotContains(response,'name="type"')
            self.assertEqual(self.client.get(self.url('deals'),{'period':'1'}).context['deals_page'].paginator.count,0)
            self.assertEqual(self.client.get(self.url('deals'),{'author':'Другой'}).context['deals_page'].paginator.count,0)
            exported=self.client.get(self.url('deals'),{'period':'2','export':'xlsx'})
            book=load_workbook(BytesIO(b''.join(exported.streaming_content)))
            self.assertEqual(book['Отчёт'].cell(2,8).value,25.5)
            book.close();exported.close()

    def test_macro_removes_author_dimension_but_other_filters_remain(self):
        response=self.client.get(self.url('executive'),{'author':'someone else','type':'Политики','period':'1'})
        self.assertEqual(response.context['ledger']['total'],90)
        self.assertNotContains(response,'Иванов')
        self.assertNotContains(response,'name="author"')
        self.assertEqual(response.context['macro_rows'][0]['total'],90)

    def test_filtered_export_numeric_and_personal_links_stay_inside_run(self):
        response=self.client.get(self.url('mine'),{'author':'Иванов','type':'Политики','period':'1'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['ledger']['total'],90)
        groups=response.context['presentation']['product_sections'][0]['groups']
        link=groups[0]['children'][0]['detail_url']
        self.assertIn(str(self.run.pk),link)
        self.assertNotIn('/reports-next/detail/',link)
        self.assertEqual(self.client.get(self.url('team'),{'author':'missing','type':'Политики'}).context['ledger']['total'],0)
        self.assertEqual(self.client.get(self.url('team'),{'search':'missing','type':'Политики'}).context['ledger']['total'],0)

    def test_failed_partial_run_is_not_published_and_status_is_readonly(self):
        self.run.state='failed';self.run.save(update_fields=['state'])
        with self.assertRaises(ValidationError):reports.data_for(self.run,QueryDict())
        response=self.client.get(self.url('team'),{'export':'xlsx'})
        self.assertEqual(response.status_code,200);self.assertNotIn('ledger',response.context)
        self.assertEqual(self.client.post(reverse('motivation_shareholder_status',args=[self.run.pk])).status_code,405)

    def test_separate_modes_merge_only_q4_and_year(self):
        self.run.scenario='separate';self.run.save(update_fields=['scenario'])
        row=dict(detail_rows()[0],Подразделение='ТЕРМОТРЕЙД',АвторСделки='Separate',СуммаМотивацииUSDУчетПроцента=12)
        ShareholderSnapshot.objects.create(sequence=0,calculation=self.run,mode='separate',dataset='report_pol',payload=runner.serial([row,dict(row,КварталМотивации='Год')]))
        data=reports.data_for(self.run,QueryDict('type=Политики&period=1&period=5'))
        self.assertEqual({(r['author'],r['code']) for r in data['rows']},{('Separate','1'),('Иванов','5')})
        self.assertEqual(data['total'],102)

    def test_form_and_permissions_and_catalog(self):
        self.assertContains(self.client.get(reverse('motivation_reports')+'?section=additional'),'Отчёты для акционеров')
        from .shareholder_forms import CalculationForm
        form=CalculationForm({'name':'x','as_of':'2027-01-01','scenario':'combined','quarters':['1'],'threshold_percent':'1','source_job':self.job.pk})
        self.assertFalse(form.is_valid());self.assertIn('as_of',form.errors)
        user=User.objects.create_user('outsider');self.client.force_login(user)
        self.assertEqual(self.client.get(self.url('team')).status_code,403)
        self.assertEqual(self.client.get(reverse('motivation_shareholders')).status_code,403)

    @patch('admin_motivation.services.shareholder_runner.launch')
    def test_new_run_freezes_parameters_and_launches_only_after_commit(self,launch):
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(reverse('motivation_shareholders'),{'name':'New','as_of':'2026-10-01','scenario':'combined','quarters':['1','2','3'],'threshold_percent':'1','source_job':self.job.pk})
        self.assertEqual(response.status_code,302);launch.assert_called_once()
        run=ShareholderCalculation.objects.get(name='New');self.assertEqual(run.quarters,[1,2,3]);self.assertEqual(run.configuration['plan_version'],'Plan')
        self.job.plan_version='Changed';self.job.save();run.refresh_from_db();self.assertEqual(run.configuration['plan_version'],'Plan')

    @patch('admin_motivation.services.shareholder_runner.execute',side_effect=RuntimeError('secret connection string'))
    def test_worker_failure_marks_unpublished_result_and_hides_internal_error(self,execute):
        self.run.state='queued';self.run.save(update_fields=['state'])
        runner.run_calculation(self.run.pk)
        self.run.refresh_from_db();self.assertEqual(self.run.state,'failed');self.assertNotIn('secret',self.run.error)
        with self.assertRaises(ValidationError):runner.run_calculation(self.run.pk)

class CompiledProcedureTests(TestCase):
    def test_real_preparation_pipeline_can_be_adapted_without_persistent_output_writes(self):
        from pathlib import Path
        from unittest.mock import MagicMock
        from .services import calculation_jobs
        source=(Path(__file__).parent/'sql/review/sp_CalcBySub.refactored.sql').read_text()
        source=source[source.index('CREATE OR ALTER PROCEDURE'):].replace('@Year','@FactYear')
        source=source.replace("@PlanVersion nvarchar(255) = N'План продаж [Q1_2024] v4'","@PlanVersion nvarchar(255),@QuarterShare decimal(9,8),@YearShare decimal(9,8),@PayoutThreshold decimal(9,8)")
        for variant,mode in [('all_2026','combined'),('separate_2026','separate')]:
            cursor=MagicMock();cursor.fetchone.return_value=(source,None);cursor.fetchall.return_value=[('SubdivisionKey',),('SubdivisionName',)]
            with patch.object(calculation_jobs,'rules',return_value=[]):calculation_jobs.prepare(cursor,variant)
            compiled=cursor.execute.call_args.args[0]
            sql=private_sql(compiled,mode,'abc')
            self.assertNotIn('DataWH.motivation.ReportMotivationByDealAuthor',sql)
            self.assertNotIn('DataWH.motivation.PlanFact',sql)
            self.assertNotIn('DataWH.motivation.AnnualAccrual',sql)
            self.assertNotIn('FROM DataWH.dbo.Sales',sql)
            self.assertNotIn('UPDATE DataWH.dbo.Sales',sql)
            self.assertRegex(sql,r'FROM\s+#share_transformed\s+s')
            self.assertIn('FROM #share_subdivisions',sql)
            self.assertIn('SELECT * INTO #portal_motivation_sales',sql)
            self.assertIn('SELECT @PortalPreparedRun AS SharePreparedRun',sql)

class WorkerSnapshotTests(TestCase):
    setUp=SnapshotTests.setUp
    url=SnapshotTests.url
    def test_executor_publishes_only_after_every_dataset_and_releases_shared_lock(self):
        from uuid import uuid4
        connection,cursor=Mock(),Mock();prepared=uuid4()
        cursor.fetchone.side_effect=[(0,),(prepared,'2026-11-20',100),('compiled',)]
        cursor.description=[('SharePreparedRun',)]
        cursor.nextset.return_value=False
        # Final SELECT of adapted batch is read after the three startup queries.
        cursor.fetchone.side_effect=[(0,),(prepared,'2026-11-20',100),('compiled',),(prepared,)]
        self.run.state='running';self.run.save(update_fields=['state'])
        seen=[]
        def save(run,cur,mode,dataset,sql,*args,**kwargs):
            run.refresh_from_db();self.assertEqual(run.state,'running');seen.append(dataset)
            return 1
        with patch.object(runner,'prepare_mapping',return_value=[]),patch.object(runner,'private_sql',return_value='SELECT test'),patch.object(runner,'save_query',side_effect=save):
            runner.execute(self.run,lambda *args:(connection,cursor))
        self.run.refresh_from_db();self.assertEqual(self.run.state,'completed');self.assertEqual(len(seen),17)
        self.assertTrue({'deals_pol','deals_sales','deals_dir'}.issubset(seen))
        self.assertEqual(self.run.metadata['closed_deals_version'],'closed-deals-v1')
        self.assertEqual(self.run.metadata['prepared_run'],str(prepared));connection.close.assert_called_once()
        self.assertIn("@LockMode=N'Shared'",cursor.execute.call_args_list[0].args[0])

    def test_executor_closes_connection_on_late_partial_snapshot_failure(self):
        from uuid import uuid4
        connection,cursor=Mock(),Mock();prepared=uuid4()
        cursor.fetchone.side_effect=[(0,),(prepared,'2026-11-20',100),('compiled',),(prepared,)]
        cursor.description=[('SharePreparedRun',)];cursor.nextset.return_value=False
        self.run.state='running';self.run.save(update_fields=['state'])
        with patch.object(runner,'prepare_mapping',return_value=[]),patch.object(runner,'private_sql',return_value='SELECT test'),patch.object(runner,'save_query',side_effect=[1,1,RuntimeError('disk')]):
            with self.assertRaises(RuntimeError):runner.execute(self.run,lambda *args:(connection,cursor))
        self.run.refresh_from_db();self.assertEqual(self.run.state,'running');connection.close.assert_called_once()

class FilteredPerformanceTests(SimpleTestCase):
    def test_personal_product_bases_follow_filters_but_global_base_is_preserved(self):
        row=dict(detail_rows()[0],VariationCalculate='Марка(Бренд)')
        personal=reports.filtered_performance([row,row],[])
        self.assertEqual(len(personal),1);self.assertEqual(personal[0]['Plan_'],600)
        self.assertEqual(personal[0]['ScopeAuthor'],'Иванов')
        global_row=dict(row,VariationCalculate='Подразделение(Глобально)')
        stored={**global_row,'ScopeAuthor':'','Plan_':'50000','Fact_':'25000'}
        self.assertEqual(reports.filtered_performance([global_row],[stored])[0]['Plan_'],'50000')
        self.assertEqual(reports.filtered_performance([], [stored]),[])


class ShareholderReadPerformanceTests(TestCase):
    setUp=SnapshotTests.setUp

    def test_summary_matches_full_details_for_both_currencies(self):
        for currency in ('USD','RUB'):
            query=QueryDict('type=Политики&period=1&period=5&currency='+currency)
            full=reports.data_for(self.run,query)
            with patch('admin_motivation.services.shareholder_reports.manager_detail_report',side_effect=AssertionError('Summary must not build product details')):
                summary=reports.data_for(self.run,query,detailed=False)
            for key in ('rows','total','plan_fact','department_plan_fact'):
                self.assertEqual(summary[key],full[key])

    def test_completed_filter_options_are_cached_without_snapshot_queries(self):
        from django.utils import timezone
        from django.test import override_settings
        self.run.completed_at=timezone.now()
        with override_settings(CACHES={'default':{'BACKEND':'django.core.cache.backends.locmem.LocMemCache','LOCATION':'shareholder-filter-test'}}):
            first=reports.options(self.run)
            with self.assertNumQueries(0):
                self.assertEqual(reports.options(self.run),first)

    def test_index_only_loads_list_fields_and_omits_workflow(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get(reverse('motivation_shareholders'))
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['skip_motivation_workflow'])
        table=ShareholderCalculation._meta.db_table
        selects=[q['sql'] for q in queries if table in q['sql'] and q['sql'].lstrip().upper().startswith('SELECT')]
        self.assertTrue(selects)
        self.assertTrue(all('configuration' not in sql and 'metadata' not in sql for sql in selects))


    def test_shareholder_team_explicitly_shows_zero_quarter_catchup(self):
        url=reverse('motivation_shareholder_report',args=[self.run.pk,'team'])
        response=self.client.get(url,{'period':['1','5'],'type':'Политики'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Доплата за кварталы, USD')
        self.assertContains(response,'Доплата за кварталы',count=3)
        self.assertEqual(response.context['presentation']['bonus'],0)
        for section in response.context['presentation']['team_sections']:
            self.assertEqual(section['bonus'],0)
            for row in section['rows']:self.assertEqual(row['bonus'],0)
        exported=self.client.get(url,{'period':['1','5'],'type':'Политики','export':'xlsx'})
        book=load_workbook(BytesIO(b''.join(exported.streaming_content)))
        values=list(book['Сводка'].values)
        index=next(i for i,row in enumerate(values) if 'Годовая доплата' in row)
        column=values[index].index('Годовая доплата')
        bonuses=[row[column] for row in values[index+1:] if isinstance(row[column],(int,float))]
        self.assertTrue(bonuses)
        self.assertTrue(all(value==0 for value in bonuses))
        book.close();exported.close()
