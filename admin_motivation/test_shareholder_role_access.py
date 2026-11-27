from datetime import date
from decimal import Decimal
from io import BytesIO
from copy import deepcopy
from django.contrib.auth.models import User,Group
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from base.access import ensure_roles,BRANCH_DIRECTOR,DEPUTY_DIRECTOR,POLITICIAN,SALES
from base.models import PortalReportGroup,MotivationReportAccess
from .models import Chanel,Subdivision,TypeCoeff,ShareholderSnapshot
from . import test_shareholders as fixtures
from .test_manager_detail import detail_rows
from .services import shareholder_reports,shareholder_runner

OWN='Собственный Директор'
OTHER='Чужой Сотрудник'


class ShareholderRoleAccessTests(TestCase):
    def setUp(self):
        fixtures.SnapshotTests.setUp(self);ensure_roles()
        self.run.chunks.all().delete()
        channel=Chanel.objects.create(chanel_name='Scoped snapshots')
        self.branches={name:Subdivision.objects.create(subdivision_key=name,subdivision_name=name,subdivision_global=name,chanel=channel) for name in ('A','B')}
        self.types={name:TypeCoeff.objects.get_or_create(type_coeff_name=name)[0] for name in ('Политики','Продажи','Директора','Кураторы')}
        for kind,tag in shareholder_reports.KINDS.items():
            payload=[{**row,'Подразделение':branch,'АвторСделки':author,'ТипКоэф':kind} for branch in self.branches for author in (OWN,OTHER) for row in detail_rows()]
            ShareholderSnapshot.objects.create(calculation=self.run,dataset='report_'+tag,mode='combined',sequence=0,payload=shareholder_runner.serial(payload))
        self.global_options=shareholder_reports.options(self.run)

    def login_role(self,role):
        user=User.objects.create_user('scope-'+str(User.objects.count()),last_name='Собственный',first_name='Директор')
        user.groups.add(Group.objects.get(name=role))
        group=Group.objects.create(name='Allowed group '+str(user.pk))
        scope=PortalReportGroup.objects.create(group=group,employee_type=self.types['Политики'])
        scope.subdivisions.add(self.branches['A']);user.groups.add(group)
        if role==POLITICIAN:MotivationReportAccess.objects.create(user=user,subdivision=self.branches['A'],deal_author=OWN)
        self.client.force_login(user)
        return user

    def url(self,kind):return reverse('motivation_shareholder_report',args=[self.run.pk,kind])

    def test_politician_uses_own_identity_and_scoped_filter_options_despite_global_cache(self):
        self.login_role(POLITICIAN)
        response=self.client.get(self.url('mine'),{'period':'1'})
        self.assertEqual(response.status_code,200)
        self.assertEqual({(r['subdivision'],r['author'],r['kind']) for r in response.context['ledger']['rows']},{('A',OWN,'Политики')})
        self.assertEqual([r['value'] for r in response.context['authors']],[OWN])
        self.assertEqual([r['value'] for r in response.context['type_options']],['Политики'])
        self.assertNotContains(response,OTHER)
        self.assertFalse(any(i['url'].split('?')[0]==self.url('executive') for i in response.context['navigation']))
        self.run.refresh_from_db();self.assertEqual(self.run.metadata[shareholder_reports.OPTIONS_KEY],self.global_options)
        self.assertContains(self.client.get(reverse('index_admin_motivation')),'Отчёты для акционеров')
        self.assertContains(self.client.get(reverse('motivation_shareholders')),self.url('mine'))

    def test_tampered_author_branch_type_and_global_report_are_denied(self):
        self.login_role(POLITICIAN)
        for query in ({'author':OTHER},{'subdivision':'B'},{'type':'Директора'},{'author':[OWN,OTHER]}):
            for export in ('','xlsx'):
                response=self.client.get(self.url('detail'),{**query,'export':export})
                self.assertEqual(response.status_code,403,(query,export))
        self.assertEqual(self.client.get(self.url('team')).status_code,403)
        self.assertEqual(self.client.get(self.url('executive')).status_code,403)

    def test_director_sees_team_only_in_assigned_branches_and_allowed_types(self):
        self.login_role(BRANCH_DIRECTOR)
        response=self.client.get(self.url('team'),{'period':'1'})
        self.assertEqual(response.status_code,200)
        rows=response.context['ledger']['rows']
        self.assertEqual({r['subdivision'] for r in rows},{'A'})
        self.assertEqual({r['author'] for r in rows},{OWN,OTHER})
        self.assertEqual({r['kind'] for r in rows},{'Политики','Продажи','Директора'})
        self.assertEqual(self.client.get(self.url('team'),{'subdivision':'B'}).status_code,403)
        self.assertEqual(self.client.get(self.url('team'),{'type':'Кураторы'}).status_code,403)

    def test_deputy_sees_all_politicians_but_only_own_director_premium(self):
        self.login_role(DEPUTY_DIRECTOR)
        response=self.client.get(self.url('team'),{'period':'1'})
        self.assertEqual(response.status_code,200)
        rows=response.context['ledger']['rows']
        self.assertEqual({r['author'] for r in rows if r['kind']=='Политики'},{OWN,OTHER})
        self.assertEqual({r['author'] for r in rows if r['kind']=='Директора'},{OWN})
        self.assertEqual({r['kind'] for r in rows},{'Политики','Директора'})
        self.assertEqual(self.client.get(self.url('detail'),{'type':'Директора','author':OTHER}).status_code,403)

    def test_excel_download_contains_only_accessible_identity_and_branch(self):
        self.login_role(POLITICIAN)
        response=self.client.get(self.url('registry'),{'period':'1','export':'xlsx'})
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(b''.join(response.streaming_content)));self.addCleanup(book.close);response.close()
        values=[str(v) for ws in book for row in ws.values for v in row if v is not None]
        self.assertIn(OWN,values);self.assertNotIn(OTHER,values)
        self.assertNotIn('B',values)

    def test_read_only_users_cannot_create_archive_or_restore_runs(self):
        self.login_role(BRANCH_DIRECTOR)
        self.assertEqual(self.client.post(reverse('motivation_shareholders'),{}).status_code,403)
        for action in ('archive','restore'):
            self.assertEqual(self.client.post(reverse('motivation_shareholder_archive',args=[self.run.pk]),{'action':action}).status_code,403)
        self.assertEqual(self.client.get(reverse('motivation_shareholder_status',args=[self.run.pk])).status_code,200)

    def test_missing_scope_and_unrequested_sales_role_stay_denied(self):
        for role in (POLITICIAN,BRANCH_DIRECTOR,DEPUTY_DIRECTOR,SALES):
            user=User.objects.create_user('unscoped-'+role);user.groups.add(Group.objects.get(name=role));self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('motivation_shareholders')).status_code,403)

    def test_branch_type_rules_do_not_form_a_cartesian_grant(self):
        user=self.login_role(POLITICIAN)
        group=Group.objects.create(name='Sales only in B')
        scope=PortalReportGroup.objects.create(group=group,employee_type=self.types['Продажи']);scope.subdivisions.add(self.branches['B']);user.groups.add(group)
        response=self.client.get(self.url('registry'),{'period':'1'})
        self.assertEqual(response.status_code,200)
        self.assertEqual({(r['subdivision'],r['kind'],r['author']) for r in response.context['ledger']['rows']},{('A','Политики',OWN),('B','Продажи',OWN)})

    def test_closed_deals_choices_csv_xlsx_and_partner_rows_are_scoped(self):
        self.run.metadata['closed_deals_version']='closed-deals-v1';self.run.save(update_fields=['metadata'])
        payload=[{'Подразделение':branch,'АвторСделки':author,'ТипКоэф':'Политики','ДатаЗакрытияСделки':'2026-01-15','ДатаОтгрузки':'2026-01-15','Номер':branch+'-'+author,
                  'Партнер':'Партнер '+author,'СуммаОтгрузокUSD':'100','СуммаКвМотивацииUSD':'10','СуммаМотивацииUSDГод':'2'} for branch in self.branches for author in (OWN,OTHER)]
        ShareholderSnapshot.objects.create(calculation=self.run,dataset='deals_pol',mode='combined',sequence=0,payload=payload)
        self.login_role(POLITICIAN)
        for query in ({'_choices':'1'},{'run':'1'},{'run':'1','_partner_rows':'1','_partner':'Партнер '+OWN},{'run':'1','export':'csv'},{'run':'1','export':'xlsx'}):
            response=self.client.get(self.url('deals'),query)
            self.assertEqual(response.status_code,200,query)
            if query.get('export')=='xlsx':
                book=load_workbook(BytesIO(b''.join(response.streaming_content)));text=str([list(ws.values) for ws in book]);book.close();response.close()
            else:text=(b''.join(response.streaming_content) if response.streaming else response.content).decode('utf-8-sig');response.close()
            self.assertNotIn(OTHER,text,query)
        self.assertEqual(self.client.get(self.url('deals'),{'_choices':'1','subdivision':'B'}).status_code,403)

    def test_forecast_rows_and_full_kind_execution_use_separate_permission_paths(self):
        from .services.plan_premium_forecast import VERSION
        self.run.metadata['plan_forecast_version']=VERSION
        self.run.save(update_fields=['metadata'])
        chunk=self.run.chunks.get(dataset='report_pol')
        for row in chunk.payload:
            if row['АвторСделки']==OTHER:row['СуммаОтгрузокUSDКвартал']=1000
        chunk.save(update_fields=['payload'])
        plans=[{'Подразделение':branch,'АвторСделки':author,'Год':2026,'Квартал':1,'ДатаЗакрытияСделки':'2026-01-01',
                'ТипКоэф':'Политики','ГруппаПлановПродаж':'RAC','ГруппаТоваров':'G','Марка':'B','СуммаПланUSD':1000,
                **{f'K{i}':'.01' for i in range(5)}} for branch in self.branches for author in (OWN,OTHER)]
        ShareholderSnapshot.objects.create(calculation=self.run,dataset='forecast_plan_pol',mode='combined',sequence=0,payload=plans)
        ShareholderSnapshot.objects.create(calculation=self.run,dataset='forecast_history',mode='combined',sequence=0,payload=[{'Подразделение':'A','ГруппаПлановПродаж':'RAC','ГруппаТоваров':'G','Марка':'B','Segment':0,'USD':100}])
        self.login_role(POLITICIAN)
        response=self.client.get(self.url('mine'),{'period':'1'})
        self.assertEqual(response.status_code,200)
        ledger=response.context['ledger']
        self.assertEqual({(r['Подразделение'],r['АвторСделки']) for r in ledger['plan_forecast_rows']},{('A',OWN)})
        self.assertEqual(ledger['sections'][0]['data']['coefficient_performance'][0]['ratio'],Decimal('.9'))
        self.assertNotContains(response,OTHER)

    def test_dataset_type_remains_authoritative_if_legacy_row_type_is_wrong(self):
        chunk=self.run.chunks.get(dataset='report_dir')
        for row in chunk.payload:row['ТипКоэф']='Политики'
        chunk.save(update_fields=['payload'])
        self.login_role(DEPUTY_DIRECTOR)
        response=self.client.get(self.url('team'),{'period':'1'})
        self.assertEqual(response.status_code,200)
        directors=[r for r in response.context['ledger']['rows'] if r['kind']=='Директора']
        self.assertEqual({r['author'] for r in directors},{OWN})

    def test_legacy_audit_trace_is_limited_to_permitted_assigned_authors(self):
        ShareholderSnapshot.objects.create(calculation=self.run,dataset='deals',mode='combined',sequence=0,payload=[
            {'SourceBranch':branch,'AssignedAuthor':author,'OriginalAuthor':author,'ClosedDate':'2026-01-15','DocumentNumber':branch+'-'+author,
             'ProductCode':'SKU','Weight_':'1','AllocatedUSD':'10','AllocatedRUB':'900'} for branch in self.branches for author in (OWN,OTHER)])
        self.login_role(POLITICIAN)
        response=self.client.get(self.url('detail'),{'audit':'1','period':'1'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,OWN);self.assertNotContains(response,OTHER)
        self.assertTrue(all(r['SourceBranch']=='A' for r in response.context['audit_page']))

    def test_admin_preview_uses_target_scope_and_restores_admin_after_exit(self):
        admin=self.user
        target=self.login_role(POLITICIAN)
        self.client.force_login(admin)
        response=self.client.post(reverse('portal_user_preview'),{'user':target.pk})
        self.assertEqual(response.status_code,302)
        response=self.client.get(self.url('mine'),{'period':'1'})
        self.assertEqual(response.status_code,200)
        self.assertEqual({(r['subdivision'],r['author']) for r in response.context['ledger']['rows']},{('A',OWN)})
        self.assertEqual(self.client.post(reverse('motivation_shareholders'),{}).status_code,403)
        self.client.post(reverse('portal_stop_preview'))
        response=self.client.get(self.url('team'),{'period':'1'})
        self.assertEqual(response.status_code,200)
        self.assertEqual({r['subdivision'] for r in response.context['ledger']['rows']},{'A','B'})
