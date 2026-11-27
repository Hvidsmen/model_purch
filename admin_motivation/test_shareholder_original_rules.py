"""Original shareholder scheme: real SQL transformation plus immutable report totals."""
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from unittest import skipUnless
import os
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from openpyxl import load_workbook
from .services import shareholder_original_rules as original
from .services import shareholder_reports as reports, calculation_jobs
from .services.shareholder_sql import private_sql, temporary_tables
from . import test_shareholders as legacy_tests
from .models import ShareholderSnapshot
from .services.shareholder_runner import serial


def row(code, possible, paid, *, branch='Д-Москва', brand='A'):
    return {'Подразделение':branch,'АвторСделки':'Иванов','Год':2026,
        'ГруппаПлановПродаж':'1. RAC','ГруппаТоваров':'Бытовые кондиционеры','Марка':brand,
        'ТипКоэф':'Политики','VariationCalculate':'Подразделение(Глобально)',
        'КварталМотивации':'Год' if code=='5' else code+' Квартал',
        'СуммаМотивацииUSD':str(possible),'СуммаМотивацииUSDУчетПроцента':str(paid),
        'СуммаМотивацииRUR':str(Decimal(possible)*90),'СуммаМотивацииRURУчетПроцента':str(Decimal(paid)*90),
        'Остаток':0,'ОстатокRUR':0,'СуммаПланUSD':100,'СуммаОтгрузокUSD':90}


def gate(source, eligible):
    return {**{key:source.get(key) for key in original.DIMENSIONS},'YearEligible':int(eligible)}


class OriginalRulesTests(SimpleTestCase):
    def test_paid_quarters_never_repaid_and_annual_share_is_separate(self):
        source=[row('1',100,100),row('2',200,200),row('3',300,0),row('4',400,400),row('5',250,250)]
        saved=deepcopy(source)
        result=original.attach(source,[gate(source[-1],True)])
        self.assertEqual(source,saved)
        self.assertEqual([r['PotentialCatchupUSD'] for r in result],[0,0,300,0,300])
        self.assertEqual(result[-1]['Остаток'],300)
        self.assertEqual(result[-1]['ОстатокRUR'],27000)
        self.assertEqual(sum(Decimal(r['СуммаМотивацииUSDУчетПроцента'])+r['Остаток'] for r in result),1250)

    def test_failed_year_keeps_potential_and_pays_nothing(self):
        source=[row('3',300,0),row('5',75,0)]
        result=original.attach(source,[gate(source[-1],False)])
        self.assertEqual(result[-1]['PotentialCatchupUSD'],300)
        self.assertEqual(result[-1]['Остаток'],0)

    def test_zero_annual_share_does_not_prevent_catchup(self):
        source=[row('3',300,0),row('5',0,0)]
        self.assertEqual(original.attach(source,[gate(source[-1],True)])[-1]['Остаток'],300)

    def test_separate_legacy_mode_uses_its_actual_payments_not_combined_quarter_payouts(self):
        source=[row('3',300,0),row('3',200,200,branch='ТЕРМОТРЕЙД'),row('5',125,125)]
        self.assertEqual(original.attach(source,[gate(source[-1],True)])[-1]['Остаток'],300)

    def test_curator_physical_branches_do_not_merge(self):
        source=[row('3',300,0),row('5',75,75),row('3',200,0,branch='ТЕРМОТРЕЙД'),row('5',50,0,branch='ТЕРМОТРЕЙД')]
        result=original.attach(source,[gate(source[1],True),gate(source[3],False)],curator=True)
        self.assertEqual([r['Остаток'] for r in result],[0,300,0,0])

    def test_missing_or_contradictory_eligibility_fails_closed(self):
        source=[row('3',300,0),row('5',75,0)]
        for gates in ([],[gate(source[-1],True),gate(source[-1],False)]):
            with self.assertRaises(ValidationError):original.attach(source,gates)

    def test_potential_rollup_never_adds_annual_duplicate(self):
        self.assertEqual(original.potential_total([{'code':'3','potential_bonus':Decimal(300)},{'code':'5','potential_bonus':Decimal(300)}]),300)
        self.assertEqual(original.potential_total([{'code':'5','potential_bonus':Decimal(300)}]),300)


class OriginalSQLTests(TestCase):
    def compiled(self,variant):
        source=(Path(__file__).parent/'sql/review/sp_CalcBySub.refactored.sql').read_text()
        source=source[source.index('CREATE OR ALTER PROCEDURE'):].replace('@Year','@FactYear')
        source=source.replace("@PlanVersion nvarchar(255) = N'План продаж [Q1_2024] v4'","@PlanVersion nvarchar(255),@QuarterShare decimal(9,8),@YearShare decimal(9,8),@PayoutThreshold decimal(9,8)")
        cursor=MagicMock();cursor.fetchone.return_value=(source,None);cursor.fetchall.return_value=[('SubdivisionKey',),('SubdivisionName',)]
        with patch.object(calculation_jobs,'rules',return_value=[]),patch('admin_motivation.services.curators.enabled',return_value=False):
            calculation_jobs.prepare(cursor,variant)
        return cursor.execute.call_args.args[0]

    def test_original_windows_and_explicit_annual_eligibility_are_private_and_opt_in(self):
        for variant,mode in [('all_2026','combined'),('separate_2026','separate')]:
            compiled=self.compiled(variant)
            legacy=private_sql(compiled,mode,'old')
            result=private_sql(compiled,mode,'new',original_rules=True)
            self.assertIn('(Квартал=4 OR @FactYear<>2026)',legacy)
            self.assertNotIn('(Квартал=4 OR @FactYear<>2026)',result)
            self.assertNotIn('WHERE 1=0 AND',result)
            self.assertIn('AS YearEligible INTO #share_eligibility',result)
            self.assertIn('#share_eligibility',temporary_tables(result))
            self.assertIn('@PayoutThreshold',result[result.index('SELECT DISTINCT [Подразделение]'):])
            # Catch-up is derived from the frozen scenario quarter payouts instead
            # of the native combined-only duplicate INSERT.
            self.assertRegex(result,r'IF\s+1\s*=\s*0\s+INSERT INTO\s+#share_report')
            from .services.shareholder_write_guard import validate_private_writes
            # ALLOCATION_SQL has carefully audited dynamic insert strings; guard
            # validates the adapter source before those are appended.
            self.assertNotIn('INTO DataWH.motivation.',result)

    def test_no_guess_when_installed_rules_are_incompatible(self):
        with self.assertRaises(ValidationError):original.restore('SELECT 1')
        compiled=self.compiled('all_2026')
        with self.assertRaises(ValidationError):private_sql(compiled.replace('СуммаМотивацииUSDГод','UnexpectedAnnual'), 'combined','new',original_rules=True)

    def test_performance_windows_are_changed_only_in_new_shareholder_queries(self):
        from .services.motivation_performance import grouped_sql,department_sql
        for builder in (grouped_sql,department_sql):
            sql=builder('SELECT * FROM #share_report')
            self.assertIn('(pf.[Квартал]=4 OR pf.[Год]<>2026)',sql)
            changed=original.performance_sql(sql)
            self.assertNotIn('(pf.[Квартал]=4 OR pf.[Год]<>2026)',changed)
            self.assertIn('pf.[Квартал] IN (1,2,3,4)',changed)


class OriginalSnapshotTests(TestCase):
    url=legacy_tests.SnapshotTests.url
    # Inherit fixtures, not legacy assertions (those still run in their own class).
    def setUp(self):
        legacy_tests.SnapshotTests.setUp(self)
        self.run.configuration['rule']=original.RULE;self.run.save(update_fields=['configuration'])
        self.run.chunks.all().delete()
        source=[row('1',100,100),row('2',200,200),row('3',300,0),row('4',400,400),row('5',250,250)]
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='report_pol',sequence=0,payload=serial(source))
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='eligibility_pol',sequence=0,payload=[gate(source[-1],True)])
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='annual_pol',sequence=0,payload=[{**r,'Квартал':int(code),'AnnualUSD':str(value),'AnnualRUB':str(value*90)} for code,value in [('1',25),('2',50),('3',75),('4',100)] for r in [source[0]]])

    def test_worker_requires_and_saves_year_gates_before_publication(self):
        from uuid import uuid4
        from .services import shareholder_runner as runner
        for missing in (False,True):
            connection,cursor=Mock(),Mock();prepared=uuid4()
            cursor.fetchone.side_effect=[(0,),(prepared,'2026-11-20',100),('compiled',),(prepared,)]
            cursor.description=[('SharePreparedRun',)];cursor.nextset.return_value=False
            self.run.state='running';self.run.save(update_fields=['state'])
            seen=[]
            def save(run,cur,mode,dataset,sql,*args,**kwargs):
                run.refresh_from_db();self.assertEqual(run.state,'running');seen.append(dataset)
                return 0 if missing and dataset.startswith('eligibility_') else 1
            with patch.object(runner,'prepare_mapping',return_value=[]),patch.object(runner,'private_sql',return_value='SELECT * INTO #plan_2024 FROM plans') as adapter,patch.object(runner,'save_query',side_effect=save):
                if missing:
                    with self.assertRaisesMessage(ValidationError,'Не сохранено годовое условие'):runner.execute(self.run,lambda *args:(connection,cursor))
                else:
                    runner.execute(self.run,lambda *args:(connection,cursor))
                    self.run.refresh_from_db();self.assertEqual(self.run.state,'completed')
                    self.assertEqual(self.run.metadata['rule'],original.RULE)
                    self.assertTrue(all('eligibility_'+tag in seen for tag in ('pol','sales','dir')))
                self.assertTrue(adapter.call_args.kwargs['original_rules'])
            connection.close.assert_called_once()

    def test_new_reports_and_exports_use_same_payable_total(self):
        for kind in ('team','mine','detail','registry','executive'):
            response=self.client.get(self.url(kind),{'type':'Политики','author':'Иванов'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.context['ledger']['total'],1250)
            self.assertEqual(response.context['presentation']['bonus'],300)
            self.assertEqual(response.context['presentation']['potential_bonus'],300)
            export=self.client.get(self.url(kind),{'type':'Политики','author':'Иванов','export':'xlsx'})
            self.assertEqual(export.status_code,200)
            book=load_workbook(BytesIO(b''.join(export.streaming_content)),data_only=True)
            sheet=book['Доплаты и годовая часть']
            self.assertEqual(sum(r[9] for r in list(sheet.values)[1:]),300)
            self.assertEqual(sum(r[10] for r in list(sheet.values)[1:]),1250)
            book.close()

    def test_period_product_and_currency_filters_do_not_recalculate_year_gate(self):
        query=QueryDict('type=Политики&period=3&currency=RUB&brand=A')
        data=reports.data_for(self.run,query,detailed=False)
        self.assertEqual(data['total'],0)
        self.assertEqual(data['rows'][0]['potential_bonus'],27000)
        self.assertEqual(data['rows'][0]['annual_accrual'],6750)
        data=reports.data_for(self.run,QueryDict('type=Политики&period=5&currency=RUB'),detailed=False)
        self.assertEqual(data['total'],49500)
        self.assertEqual(data['rows'][0]['bonus'],27000)


    @skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1','Browser checks are opt-in')
    def test_original_cards_show_potential_and_payable_separately(self):
        import os,re
        from django.conf import settings
        from playwright.sync_api import sync_playwright,expect
        static=Path(settings.BASE_DIR)/'admin_motivation/static/admin_motivation'
        css='\n'.join((static/name).read_text() for name in ('reports_next.css','reports_performance.css','shareholders.css','shareholder_cards.css'))
        js='\n'.join((static/name).read_text() for name in ('reports_next.js','report_team_tabs.js','product_filters.js','premium_overview.js'))
        responses=[(kind,self.client.get(self.url(kind),{'type':'Политики','author':'Иванов'})) for kind in ('team','mine','executive')]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                for kind,response in responses:
                    html=re.sub(r'<(?:link|script)\b[^>]*(?:href|src)=[^>]*>(?:</script>)?','',response.content.decode())
                    page.set_content(html)
                    feature=page.locator('.sh-page').evaluate('el=>el.outerHTML')
                    page.set_content('<style>body{font:14px system-ui;color:#243650;margin:20px}'+css+'</style>'+feature+'<script>'+js+'</script>')
                    expect(page.locator('.sh-page')).to_be_visible()
                    self.assertIn('Возможная доплата',page.locator('.sh-page').inner_text())
                    self.assertNotIn('доплата не начисляется',page.locator('.sh-page').inner_text())
                    if kind=='team':
                        page.locator('.nr-team-calculation-details>summary').click()
                        expect(page.locator('.nr-team-period-card').filter(has=page.get_by_role('heading',name='3 квартал',exact=True))).to_contain_text('300,00')
                    if kind=='executive':
                        expect(page.locator('.sh-premium-card').last).to_contain_text('Доплата к выплате',ignore_case=True)
                        page.screenshot(path='/tmp/shareholder-original-cards.png',full_page=True)
                        page.set_viewport_size({'width':390,'height':844})
                        self.assertTrue(page.locator('.sh-executive').evaluate('el=>el.scrollWidth<=el.clientWidth+1'))
                self.assertEqual(errors,[])
            finally:browser.close()
