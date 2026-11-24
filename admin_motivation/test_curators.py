from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import Mock,patch
from django.contrib.auth.models import User,Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import QueryDict
from django.test import TestCase,SimpleTestCase
from django.urls import reverse
from openpyxl import load_workbook
from base.access import CURATOR
from base.report_access import politician_scope,scoped_values
from base.testing import authorize_admin
from .models import CuratorEmployee,CuratorCoefficient,GlobalCoeffVersion,Goods,Subdivision,Chanel,ShareholderCalculation,MotivationCalculationSchedule,ShareholderSnapshot
from .services import curators,curator_sql
from .services.manager_detail import manager_detail_report
from .services.reports_next_presenter import personal_details,present
from .test_manager_detail import detail_rows


class CuratorTests(TestCase):
    def setUp(self):
        self.admin=authorize_admin(self.client)
        channel=Chanel.objects.create(chanel_name='Кураторские тесты')
        self.a=Subdivision.all_objects.create(subdivision_key='Д-Москва',subdivision_name='Москва',subdivision_global='Москва',chanel=channel)
        self.b=Subdivision.all_objects.create(subdivision_key='ТЕРМОТРЕЙД',subdivision_name='ТТ',subdivision_global='ТТ',chanel=channel,merged_into=self.a)
        self.employee=CuratorEmployee.objects.create(full_name='Куратор Первый')
        self.employee.assigned_subdivisions().add(self.a,self.b)
        self.version=GlobalCoeffVersion.objects.create(effective_from=date(2026,1,1),title='Кураторская версия')
        self.good=Goods.objects.create(goods_key='CURATOR-TEST',planning_group_sales='1. RAC',group='Split',brand='AX')

    def save(self,branch=None,values=None):
        self.version.refresh_from_db()
        return curators.save_rates(self.version.pk,self.employee,branch or self.a,[{'goods':self.good.pk,**dict.fromkeys(curators.KEYS,values or '2.5')}],self.version.revision)

    def approve(self):
        from django.db import models
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=self.version.pk),status='approved')
        self.version.refresh_from_db()

    def test_two_branches_have_independent_rates_and_version_copy(self):
        self.save(self.a,'2');self.save(self.b,'5')
        self.assertEqual(list(CuratorCoefficient.objects.filter(employee=self.employee).order_by('subdivision_id').values_list('k0',flat=True)),[Decimal('.02'),Decimal('.05')])
        from .services.versions import create_version
        copied=create_version(date(2026,2,1),source_version=self.version.pk)
        self.assertEqual(CuratorCoefficient.objects.filter(version=copied).count(),2)

    def test_assignment_revision_range_and_approved_version_are_enforced(self):
        rows=[{'goods':self.good.pk,**dict.fromkeys(curators.KEYS,'3')}]
        with self.assertRaises(ValidationError):curators.save_rates(self.version.pk,self.employee,self.a,rows,'stale')
        self.employee.assigned_subdivisions().remove(self.b)
        with self.assertRaises(ValidationError):self.save(self.b)
        for value in ('100.0001','NaN','1.00001'):
            with self.assertRaises(ValidationError):self.save(values=value)
        self.assertEqual(CuratorCoefficient.objects.count(),0)
        self.save();self.approve()
        with self.assertRaises(ValidationError):self.save()

    def test_excel_is_atomic_and_does_not_accept_formulas_or_fractional_ids(self):
        output=curators.workbook(self.employee,self.a,self.version)
        book=load_workbook(output);sheet=book['Кураторы']
        for column in range(3,8):sheet.cell(2,column,'2.5')
        buffer=BytesIO();book.save(buffer);book.close()
        rows=curators.read_upload(SimpleUploadedFile('rates.xlsx',buffer.getvalue()))
        self.assertEqual(len(rows),1)
        self.save(values='2.5')
        self.assertEqual(CuratorCoefficient.objects.get().k0,Decimal('.025'))
        rows[0]['goods']=Decimal('1.5')
        self.version.refresh_from_db()
        with self.assertRaises(ValidationError):curators.save_rates(self.version.pk,self.employee,self.a,rows,self.version.revision)
        book=load_workbook(BytesIO(buffer.getvalue()));book['Кураторы'].cell(2,3,'=2+2');bad=BytesIO();book.save(bad);book.close()
        with self.assertRaises(ValidationError):curators.read_upload(SimpleUploadedFile('bad.xlsx',bad.getvalue()))

    def test_personal_scope_comes_from_authenticated_user_and_physical_assignments(self):
        user=User.objects.create_user('curator-user');user.groups.add(Group.objects.get(name=CURATOR))
        self.employee.user=user;self.employee.save()
        scope=politician_scope(user)
        self.assertEqual(set(scope['subdivision']),{'Д-Москва','ТЕРМОТРЕЙД'})
        values=scoped_values('manager-detail',{'TypeCoeff_':'Кураторы','Subdivision':['Чужой филиал'],'DealAuthor':['Чужой автор']},scope)
        self.assertEqual(values['DealAuthor'],['Куратор Первый'])
        self.assertEqual(set(values['Subdivision']),{'Д-Москва','ТЕРМОТРЕЙД'})
        self.client.force_login(user)
        self.assertEqual(self.client.post(reverse('motivation_curators'),{'full_name':'Injected'}).status_code,403)
        self.employee.is_archived=True;self.employee.save()
        self.assertIsNone(politician_scope(user))

    def test_export_keeps_curators_separate_and_does_not_merge_physical_rates(self):
        self.save(self.a,'2');self.save(self.b,'5');self.approve()
        from .services.sql_export import export_motivation,COEFFICIENT_INSERT
        connection,cursor=Mock(),Mock()
        with patch('admin_motivation.services.execution_tracking.record_export'):
            export_motivation(lambda *args:(connection,cursor),manage_connection=False)
        rows=[row for call in cursor.executemany.call_args_list if call.args[0]==COEFFICIENT_INSERT for row in call.args[1]]
        self.assertEqual({row[3] for row in rows},{'Кураторы'})
        self.assertEqual({row[1] for row in rows},{'Д-Москва','ТЕРМОТРЕЙД'})
        self.assertEqual(len(rows),10)
        from .services.workflow import export_snapshot
        import json
        json.dumps(export_snapshot())

    def test_approval_snapshot_displays_curator_identity_and_detects_assignment_changes(self):
        self.save()
        from .services.review_workflow import snapshot,cells
        data=snapshot(self.version);items=[v for k,v in cells(data).items() if k[0]=='curatorcoefficient']
        self.assertTrue(items);self.assertEqual(items[0]['employee'],'Куратор Первый')
        from .services.approval import state_digest
        before=state_digest(self.version,None);self.employee.assigned_subdivisions().remove(self.b)
        self.assertNotEqual(state_digest(self.version,None),before)

    def test_directory_and_rates_pages_have_excel_and_readonly_history(self):
        self.assertContains(self.client.get(reverse('motivation_curators')),'Куратор Первый')
        url=reverse('motivation_curator_rates',args=[self.employee.pk])
        response=self.client.get(url,{'version':self.version.pk,'subdivision':self.b.pk})
        self.assertContains(response,'Сохранить строку');self.assertContains(response,'Загрузить Excel')
        self.approve();response=self.client.get(url,{'version':self.version.pk,'subdivision':self.b.pk})
        self.assertNotContains(response,'Сохранить строку')

    def test_personal_report_keeps_all_assigned_branches_and_hides_aggregate_execution(self):
        from contextlib import nullcontext
        from uuid import uuid4
        from django.utils import timezone
        from .models import MotivationExecution
        user=User.objects.create_user('own-curator-report');user.groups.add(Group.objects.get(name=CURATOR))
        self.employee.user=user;self.employee.save();self.client.force_login(user)
        rows=[dict(detail_rows()[1],Подразделение=branch,АвторСделки=self.employee.full_name,ТипКоэф='Кураторы',VariationCalculate='Подразделение(Глобально)',СуммаПланUSD=100,СуммаОтгрузокUSD=fact,СуммаМотивацииUSDУчетПроцента=paid) for branch,fact,paid in [('Д-Москва',120,120),('ТЕРМОТРЕЙД',60,0)]]
        data=manager_detail_report(rows,performance_rows=rows)
        run=MotivationExecution.objects.create(run_id=uuid4(),job_key=uuid4(),state='completed',started_at=timezone.now(),completed_at=timezone.now(),parameters={'fact_year':2026,'plan_version':'Plan','quarter_share':'80','year_share':'20','payout_threshold':'.8','transition_rule':'moscow-tt-q4-policy-heat-sales-dpp-v2'})
        with patch('admin_motivation.services.execution_tracking.latest_for_report',return_value=run),patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()),patch('admin_motivation.services.reports_next.load_report',return_value=data) as load:
            response=self.client.get(reverse('motivation_report_next',args=['mine']),{'run':'1'})
        self.assertEqual(response.status_code,200)
        values=load.call_args.args[1]
        self.assertEqual(set(values['Subdivision']),{'Д-Москва','ТЕРМОТРЕЙД'})
        self.assertEqual(values['DealAuthor'],[self.employee.full_name])
        self.assertEqual(response.context['presentation']['total'],120)
        self.assertIsNone(response.context['presentation']['goal_total']['ratio'])
        self.assertContains(response,'ТЕРМОТРЕЙД')
        self.assertNotContains(response,'90,00%')

    def test_sql_installation_adds_branch_jobs_and_only_appends_curator_rows(self):
        self.save();self.approve()
        source=CuratorSQLTests().definition()
        installed=curator_sql.append_to_procedure(source,'all_2026')
        self.assertIn('SELECT DISTINCT Subdivision',installed)
        self.assertIn('INSERT INTO DataWH.motivation.ReportMotivationByDealAuthorAll2026',installed)
        self.assertIn("SET [ТипКоэф]=N''Кураторы''",installed)
        self.assertIn('ROLLBACK TRANSACTION',installed)
        self.assertEqual(curator_sql.strip(installed).split(),source.split())

    def test_curator_only_version_can_follow_normal_approval_workflow(self):
        self.save()
        from .services.approval import review_approval,approve_review
        review=review_approval(self.version)
        approve_review(review['token'])
        self.version.refresh_from_db();self.assertEqual(self.version.status,'approved')

    def test_only_curator_budget_preserves_physical_branch_rate_and_no_global_fallback(self):
        self.save(self.a,'2');self.save(self.b,'5')
        from .services.director_comparison import DirectorIndex
        index=DirectorIndex([self.version.pk],local=True,coefficient_model=CuratorCoefficient,merge_aliases=False)
        amounts=[Decimal(100),Decimal(0),Decimal(0),Decimal(0),Decimal(0)]
        self.assertEqual(index.by_employee(self.version.pk,'Д-Москва','1. RAC','Split','AX',amounts)[self.employee.pk],2)
        self.assertEqual(index.by_employee(self.version.pk,'Термотрейд','1. RAC','Split','AX',amounts)[self.employee.pk],5)


class CuratorPresentationTests(SimpleTestCase):
    def test_premiums_add_but_execution_is_not_averaged_across_branches(self):
        rows=[]
        for branch,ratio,paid in [('A',Decimal('1.2'),120),('B',Decimal('.6'),0)]:
            rows.append(dict(detail_rows()[1],Подразделение=branch,АвторСделки='Куратор',ТипКоэф='Кураторы',VariationCalculate='Подразделение(Глобально)',
                СуммаПланUSD=100,СуммаОтгрузокUSD=ratio*100,СуммаМотивацииUSDУчетПроцента=paid))
        data=manager_detail_report(rows,performance_rows=rows)
        self.assertTrue(data['curator_multiple'])
        self.assertEqual(data['detail_totals'][-1][17],120)
        self.assertIsNone(data['detail_totals'][-1][16])
        ledger={'sections':[{'kind':'Кураторы','data':data}],'payout_threshold':Decimal('.8')}
        display=personal_details(ledger,QueryDict('year=2026&period=5'))
        self.assertEqual({g['branch'] for g in display['goals']},{'A','B'})
        self.assertEqual({g['ratio'] for g in display['goals']},{Decimal('1.2'),Decimal('.6')})
        self.assertIsNone(display['goal_total']['ratio'])
        self.assertEqual(display['goal_total']['calculated'],120)

    def test_curators_inherit_director_exclusions_in_python_and_sql(self):
        from .services.motivation_exclusions import allowed,sql_allowed,_current
        token=_current.set((('group','Директора','HEAT'),))
        try:
            self.assertFalse(allowed('Кураторы','HEAT'))
            self.assertEqual(sql_allowed('Кураторы'),sql_allowed('Директора'))
            self.assertIn("N'Кураторы'",sql_allowed())
        finally:_current.reset(token)

class CuratorSQLTests(SimpleTestCase):
    def definition(self):
        from .services.prepared_sales import MARKER
        return '''CREATE PROCEDURE motivation.PortalCalcAll2026
 @FactYear int,@PlanVersion nvarchar(500),@QuarterShare decimal(9,8),@YearShare decimal(9,8),@PayoutThreshold decimal(9,8) AS BEGIN
 IF @@TRANCOUNT<>0 THROW 50003,N'outer transaction',1;
 '''+MARKER+'''
 DECLARE @PortalPreparedRun uniqueidentifier;
 SELECT * INTO #portal_motivation_sales FROM DataWH.motivation.PortalPreparedSales WHERE ClosedDealDate>='20260101';
 ;WITH fact AS (SELECT s.*,sc.EmployeeName AS АвторСделки,sc.TypeCoeff AS ТипКоэф
 FROM #portal_motivation_sales s JOIN DataWH.motivation.vSubdivisionMotiveCoeffTo sc ON sc.TypeCoeff=N'Директора')
 SELECT * INTO DataWH.motivation.FactAll2026 FROM fact;
 SELECT * INTO DataWH.motivation.PlanFactAll2026 FROM DataWH.motivation.FactAll2026;
 SELECT * INTO DataWH.motivation.ReportMotivationByDealAuthorAll2026 FROM DataWH.motivation.PlanFactAll2026;
 SELECT * INTO DataWH.motivation.AnnualAccrualAll2026 FROM DataWH.motivation.ReportMotivationByDealAuthorAll2026;
 END;'''

    def test_pair_is_scoped_to_physical_branch_and_own_rates_without_reallocating_deals(self):
        batch=curator_sql.pair_batch(self.definition())
        self.assertIn('r.Subdivision=s.SubdivisionName',batch)
        self.assertNotIn("SET SubdivisionName=N'Д-Москва'",batch)
        self.assertIn('r.EmployeeCode=sc.EmployeeCode',batch)
        self.assertIn('JOIN #curator_coeff',batch)
        self.assertIn("SET TypeCoeff=N'Директора'",batch)
        self.assertIn("SET [ТипКоэф]=N'Кураторы'",batch)
        self.assertNotIn('@@TRANCOUNT<>0 THROW',batch)
        self.assertNotIn('ShareWeight',batch)
        self.assertNotIn('INTO DataWH.motivation.FactAll2026',batch)
        self.assertIn('SELECT * INTO #share_transformed FROM #portal_motivation_sales',batch)

    def test_unknown_director_source_fails_instead_of_fabricating_curator_premiums(self):
        with self.assertRaises(ValidationError):curator_sql.pair_batch(self.definition().replace('EmployeeName','Unknown'))

    def test_instrumentation_is_removed_before_private_shareholder_copy(self):
        source=self.definition();marked=source[:-4]+curator_sql.START+'\nINSERT INTO dbo.OrdinaryTable SELECT 1;\n'+curator_sql.END+'\nEND;'
        from .services.shareholder_sql import private_sql
        private=private_sql(marked,'combined','abc')
        self.assertNotIn('OrdinaryTable',private)
        self.assertNotIn(curator_sql.START,private)

class CuratorShareholderTests(TestCase):
    def test_fourth_kind_is_visible_in_all_five_snapshot_reports_and_excel(self):
        from . import test_shareholders as fixtures
        from .services.shareholder_runner import serial
        from .services.shareholder_reports import TITLES,data_for
        from .services.reports_next_presenter import present
        fixtures.SnapshotTests.setUp(self)
        rows=[dict(row,ТипКоэф='Кураторы',Подразделение=branch,АвторСделки='Куратор') for branch in ('Д-Москва','ТЕРМОТРЕЙД') for row in detail_rows()]
        ShareholderSnapshot.objects.create(sequence=0,calculation=self.run,mode='combined',dataset='report_cur',payload=serial(rows))
        self.run.scenario='separate';self.run.save(update_fields=['scenario'])
        ledger=data_for(self.run,QueryDict('type=Кураторы'))
        self.assertEqual(ledger['total'],360)
        self.assertTrue(ledger['sections'][0]['data']['curator_multiple'])
        for kind in TITLES:
            url=reverse('motivation_shareholder_report',args=[self.run.pk,kind])
            response=self.client.get(url,{'type':'Кураторы','author':'Куратор'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.context['ledger']['total'],360)
            self.assertContains(response,'ТЕРМОТРЕЙД')
            exported=self.client.get(url,{'type':'Кураторы','author':'Куратор','export':'xlsx'})
            self.assertEqual(exported.status_code,200)
            book=load_workbook(BytesIO(b''.join(exported.streaming_content)))
            self.assertGreater(len(book.sheetnames),1);book.close();exported.close()
