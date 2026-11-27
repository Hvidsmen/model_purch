from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import patch, Mock
from django.test import TestCase
from django.http import QueryDict
from . import test_shareholder_deals as frozen_fixtures, test_reports_next_deals as native_fixtures
from .services import shareholder_deals
from .services.deal_presentation import page_csv


class FrozenPartnerTests(TestCase):
    setUp=frozen_fixtures.ClosedDealsTests.setUp
    save=frozen_fixtures.ClosedDealsTests.save
    query=frozen_fixtures.ClosedDealsTests.query

    def test_group_totals_include_all_rows_returns_and_missing_partner(self):
        self.save([dict(self.row,Партнер='B') for _ in range(60)]+[dict(self.row,Партнер=None),dict(self.row,Партнер='')],sequence=1)
        data,_=shareholder_deals.build(self.run,self.query('&deal_view=partners'))
        by_name={r['partner']:r for r in data['partner_groups']}
        self.assertEqual(by_name['B']['count'],60)
        self.assertEqual(by_name['B']['shipments'],6000)
        self.assertEqual(by_name['=Partner']['shipments'],90)
        self.assertEqual(by_name['']['count'],2)
        self.assertEqual(sum(r['possible'] for r in by_name.values()),data['totals']['possible'])
        self.assertEqual(data['details'],[])
        self.assertIn('_partner_rows=1',by_name['B']['details_url'])
        response=self.client.get(self.url,self.query('&deal_view=partners'))
        self.assertContains(response,'По партнёрам')
        self.assertContains(response,'Без партнёра')
        import re
        self.assertNotRegex(response.content.decode(),r'<details class="nr-deal-authors"[^>]*\bopen\b')

    def test_partner_and_nested_row_pagination_keep_filters(self):
        self.save([dict(self.row,Партнер=f'P{i:02}') for i in range(55)],sequence=1)
        data,_=shareholder_deals.build(self.run,self.query('&deal_view=partners&page=2'))
        self.assertEqual(data['partner_count'],56);self.assertEqual(len(data['partner_groups']),6)
        self.assertIn('deal_view=partners',data['previous_url'])
        self.save([dict(self.row,Партнер='Many') for _ in range(65)],sequence=2)
        response=self.client.get(self.url,self.query('&_partner_rows=1&_partner=Many&page=2'))
        self.assertEqual(response.status_code,200)
        html=response.json()['html']
        self.assertIn('Страница 2 из 2',html)
        self.assertIn('65 строк',html)
        self.assertEqual(html.count('MODEL'),15)
        self.assertNotIn('=Partner',html)
        empty=self.client.get(self.url,self.query('&_partner_rows=1&_partner=NoSuchPartner'))
        self.assertIn('Нет сделок',empty.json()['html'])

    def test_csv_summary_is_exact_and_does_not_export_formulas(self):
        data,_=shareholder_deals.build(self.run,self.query('&deal_view=partners'))
        content=page_csv(data).content.decode('utf-8-sig')
        self.assertIn("'=Partner",content)
        self.assertIn('90',content)
        self.assertNotIn('MODEL',content)
        invalid=self.client.get(self.url,self.query('&deal_view=unknown'))
        self.assertContains(invalid,'Выберите вид отчёта')

    def test_drilldown_keeps_original_author_and_partner_filters(self):
        self.save([dict(self.row,АвторСделки='Другой',Партнер='B')],sequence=1)
        response=self.client.get(self.url,self.query('&_partner_rows=1&_partner=B&author=Иванов'))
        self.assertIn('0 строк',response.json()['html'])
        response=self.client.get(self.url,self.query('&_partner_rows=1&_partner=B&partner=%3DPartner'))
        self.assertIn('0 строк',response.json()['html'])


class NativePartnerTests(TestCase):
    setUp=native_fixtures.ClosedDealsNextTests.setUp

    def test_group_mode_passed_to_loader_and_drilldown_avoids_facet_queries(self):
        payload=native_fixtures.deal_payload()
        with patch('admin_motivation.reports_next_deals.choices',return_value=[]) as choices,patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()),patch('admin_motivation.reports_next_deals.load_report',return_value=payload) as load:
            self.client.get(self.url,{**self.query,'deal_view':'partners'})
            self.assertEqual(load.call_args.kwargs['deal_view'],'partners')
            choices.reset_mock()
            response=self.client.get(self.url,{**self.query,'deal_view':'table','_partner_rows':'1','_partner':"O'Reilly"})
            self.assertEqual(response.status_code,200)
            self.assertEqual(load.call_args.kwargs['deal_partner'],"O'Reilly")
            self.assertIn('html',response.json())
            choices.assert_not_called()

    def test_drilldown_cannot_override_employee_identity(self):
        from django.contrib.auth.models import User,Group
        from base.access import POLITICIAN
        from base.models import MotivationReportAccess
        from .models import Chanel,Subdivision
        channel=Chanel.objects.create(chanel_name='Partner access')
        branch=Subdivision.objects.create(subdivision_key='P',subdivision_name='P',subdivision_global='P',chanel=channel)
        user=User.objects.create_user('partner-scope');user.groups.add(Group.objects.get(name=POLITICIAN))
        MotivationReportAccess.objects.create(user=user,subdivision=branch,deal_author='Trusted')
        self.client.force_login(user)
        with patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()),patch('admin_motivation.reports_next_deals.load_report',return_value=native_fixtures.deal_payload()) as load:
            response=self.client.get(self.url,{**self.query,'_partner_rows':'1','_partner':'B','author':'Other','subdivision':'Other','type':'Директора'})
        self.assertEqual(response.status_code,200)
        values=load.call_args.args[1]
        self.assertEqual(values['Division'],'P');self.assertEqual(values['ManagerName'],['Trusted'])
        self.assertEqual(values['TypeCoeff'],'Политики')

    def test_native_sql_groups_full_scope_and_binds_partner_values(self):
        import sqlite3,re
        from datetime import date
        from .services.reports import load_report
        from .services.deal_presentation import COLUMNS
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.create_function('MONTH',1,lambda value:int(value[5:7]))
        numeric={'СуммаОтгрузокUSD','СуммаКвМотивацииUSD','СуммаМотивацииUSDГод'}
        db.execute('CREATE TABLE fact ('+','.join('['+key+'] '+('NUMERIC' if key in numeric else 'TEXT') for key,_,_ in COLUMNS)+',[ТипКоэф] TEXT)')
        for partner,shipments,quarterly,annual,author in [('A',100,80,20,'Worker'),('A',-10,-8,-2,'Worker'),("O'Reilly",200,160,40,'Worker'),(None,50,40,10,'Worker'),('A',999,999,999,'Hidden')]:
            db.execute('INSERT INTO fact ([АвторСделки],[Подразделение],[ТипКоэф],[ДатаЗакрытияСделки],[Партнер],[СуммаОтгрузокUSD],[СуммаКвМотивацииUSD],[СуммаМотивацииUSDГод]) VALUES (?,?,?,?,?,?,?,?)',(author,'A','Политики','2026-10-20',partner,shipments,quarterly,annual))
        queries=[]
        def fetch(cursor,sql,args=(),*rest,**kwargs):
            queries.append((sql,args))
            sql=sql.replace('DataWH.motivation.Fact','fact').replace('COUNT_BIG','COUNT')
            sql=re.sub(r"\bN(?=')",'',sql)
            sql=re.sub(r'OFFSET \? ROWS FETCH NEXT 50 ROWS ONLY','LIMIT 50 OFFSET ?',sql)
            result=db.execute(sql,args)
            return [dict(zip([c[0] for c in result.description],row)) for row in result.fetchall()]
        values=dict(ManagerName=['Worker'],Division='A',TypeCoeff='Политики',StartDate=date(2026,1,1),EndDate=date(2026,12,31),Channel=[],GPSales=[],GPGoods=[],Brend=[],Partner=[])
        with patch('admin_motivation.services.reports.connect_database',return_value=(Mock(),Mock())),patch('admin_motivation.services.reports.fetch',side_effect=fetch),patch('admin_motivation.services.closing_coefficient.ReportMultipliers.apply',side_effect=lambda rows:rows),patch('admin_motivation.services.subdivision_scope.sql_predicate',return_value='1=1'),patch('admin_motivation.services.motivation_exclusions.sql_allowed',return_value='1=1'):
            grouped=load_report('deals',values,deal_view='partners')
            by_name={r['partner']:r for r in grouped['partner_groups']}
            self.assertEqual(grouped['partner_count'],3)
            self.assertEqual(grouped['totals']['shipments'],340)
            self.assertEqual(by_name['A']['shipments'],90)
            self.assertEqual(by_name['A']['possible'],90)
            detail=load_report('deals',values,deal_partner="O'Reilly")
            self.assertEqual(detail['totals']['count'],1)
            self.assertEqual(detail['totals']['shipments'],200)
            blank=load_report('deals',values,deal_partner='')
            self.assertEqual(blank['totals']['shipments'],50)
        self.assertTrue(any("O'Reilly" in args and "O'Reilly" not in sql for sql,args in queries))

    def test_new_native_execution_requires_refresh_before_drilling(self):
        with patch('admin_motivation.reports_next_deals.load_report') as load:
            response=self.client.get(self.url,{**self.query,'_partner_rows':'1','_partner':'B','_deal_execution':'old-run'})
        self.assertEqual(response.status_code,400)
        self.assertIn('Расчёт обновился',response.json()['errors'][0])
        load.assert_not_called()
