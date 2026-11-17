from copy import deepcopy
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock,patch
from django.test import SimpleTestCase,TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from openpyxl import load_workbook
from .services.coefficient_iteration_currency import RULE,configuration,convert_money,display_rows,convert_bases
from .services.coefficient_iteration_calculation import empty_row,RateBook
from .services.coefficient_iteration_runner import execute,start,aggregate_rows,safe,directors_2025
from .services.director_scenarios import director_result
from . import test_coefficient_iteration_calculation as fixtures
inputs=fixtures.inputs
row=fixtures.row


class CurrencyMoneyTests(SimpleTestCase):
    def test_fixed_rate_multiplies_each_money_field_not_identity_or_gate(self):
        original=empty_row('full_2027',2027,'B','Продажи');original.update(quarters=[D(1),D(2),D(3),D(4)],total=D(12),annual_paid=D(2))
        before=deepcopy(original);original['rub']=convert_money(original,'81.125')
        shown=display_rows([original],'RUB')[0]
        self.assertEqual(shown['total'],D('973.5'));self.assertEqual(shown['quarters'][3],D('324.5'))
        self.assertEqual(shown['branch'],'B');self.assertEqual(original['total'],before['total'])
        bases=convert_bases([{'plan':'100','fact':'80','execution':'.8','segments':['100','0','0','0','0']}],80)
        self.assertEqual(bases[0]['execution'],'.8');self.assertEqual(bases[0]['plan'],D(8000))
        self.assertIsNone(display_rows([before],'RUB'))

    def test_rates_must_be_positive_finite_and_saved_as_decimals(self):
        self.assertEqual(configuration({'usd_rub_2026':'79.25','usd_rub_2027':'80'})['usd_rub_2026'],'79.25')
        for value in ('0','-1','NaN','Infinity','1000001'):
            with self.assertRaises(ValidationError):configuration({'usd_rub_2026':value})

    def run_director(self,currency,plan_amount=100,usd_amounts=(100,100,100,100),rub_amounts=(8000,12000,10000,6000)):
        facts=[dict(branch='B',quarter=i,planning='RAC',goods='G',brand='Brand',segment='O0',positive=D(usd),negative=0,
                    positive_rub=D(rub),negative_rub=0) for i,(usd,rub) in enumerate(zip(usd_amounts,rub_amounts),1)]
        plans=[dict(r,positive=D(plan_amount),positive_rub=0) for r in facts]
        rates=SimpleNamespace(get=lambda *args:([D('.01')]*5,'Подразделение(Глобально)','exact'),factor=lambda *args:D(1))
        actor=SimpleNamespace(pk=1,full_name='Director')
        return director_result('actual_2025',2025,False,False,actor,actor,'B',facts,plans,
            {'quarter_share':80,'year_share':20,'payout_threshold':'.8'},rates,currency=currency)

    def test_directors_use_per_deal_rub_and_preserve_usd_eligibility(self):
        usd,udetails=self.run_director('USD');rub,rdetails=self.run_director('RUB')
        self.assertEqual(usd['total'],D(4));self.assertEqual(rub['total'],D(360))
        self.assertNotEqual(rub['total'],usd['total']*80)
        self.assertEqual([r['ratio'] for r in udetails],[r['ratio'] for r in rdetails])
        blocked,_=self.run_director('RUB',plan_amount=200)
        self.assertEqual(blocked['quarters'],[D(64),0,0,0]);self.assertEqual(blocked['annual_paid'],0)
        self.assertEqual(blocked['bonus'],0)

    def test_rub_yearly_catchup_uses_rub_blocked_quarters(self):
        usd,_=self.run_director('USD',usd_amounts=(50,50,100,200),rub_amounts=(4000,6000,9000,16000))
        rub,_=self.run_director('RUB',usd_amounts=(50,50,100,200),rub_amounts=(4000,6000,9000,16000))
        self.assertEqual(usd['bonus'],D('.4'));self.assertEqual(rub['bonus'],D(48))
        self.assertEqual(rub['annual_paid'],D(70));self.assertEqual(rub['total'],D(350))

    def test_actual_native_uses_saved_rub_payout_and_annual_money(self):
        from .services.coefficient_iteration_2025 import read_2025
        cursor=Mock();cursor.nextset.return_value=False;cursor.description=[('prepared_run_id',)]
        cursor.fetchone.side_effect=[(0,),(1,2,3,None),('used-run',),('used-run','2026-11-17')]
        cursor.fetchall.side_effect=[[],[],[
            ('Политики','Д-Москва','Author','1 Квартал',D(8),0,D(8),D(640),0,D(640)),
            ('Политики','Д-Москва','Author','Год',D(2),D(1),D(2),D(180),D(90),D(180))],
            [('Политики','Д-Москва','Author',1,D(10),D(900))]]
        settings={'quarter_share':'80','year_share':'20','payout_threshold':'.8','currency_rule':RULE}
        with patch('admin_motivation.services.coefficient_iteration_2025.build_sql',return_value='native SQL') as build:
            values,_=read_2025(cursor,[row(year=2025)],RateBook(*inputs()),settings,Mock())
        build.assert_called_once_with(cursor,rub=True)
        value=values[0];self.assertEqual(value['total'],D(11));self.assertEqual(value['rub']['total'],D(910))
        self.assertEqual(value['rub']['annual_accrued'],D(180));self.assertEqual(value['rub']['annual_paid'],D(180))
        self.assertEqual(value['rub']['bonus'],D(90))
        sqls=[call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any('СуммаМотивацииRURУчетПроцента' in sql for sql in sqls))
        self.assertTrue(any('CASE WHEN [AmountRURMotive]>0' in sql for sql in sqls))
        self.assertEqual(next(call.args for call in cursor.execute.call_args_list if call.args[0]=='native SQL'),('native SQL',))

    def test_historical_director_wrapper_saves_rub_per_recipient(self):
        cursor=Mock()
        cursor.fetchall.return_value=[(sub,1,'1. RAC','Бытовые кондиционеры','DAIKIN','O1',D(100),D(0),D(9000),D(0)) for sub in ('Д-Москва','ТЕРМОТРЕЙД')]
        snapshot={'directors':[],'corrections':[],'aliases':{},'default':'Подразделение(Глобально)'}
        values,_=directors_2025(cursor,[row(year=2025),row('ТЕРМОТРЕЙД',2025)],RateBook(*inputs()),
            {'quarter_share':'80','year_share':'20','payout_threshold':'.8','currency_rule':RULE},snapshot)
        actual={r['employee']:(r['total'],r['rub']['total']) for r in values}
        self.assertEqual(actual,{'Кудицкая':(D(30),D(2700)),'Чевокин':(D(40),D(3600))})
        self.assertIn('СуммаОтгрузокRUB',cursor.execute.call_args.args[0])

    def test_native_raw_director_rub_does_not_divide_by_sales_coefficient(self):
        from .services.coefficient_iteration_2025 import build_sql
        sql=build_sql(rub=True)
        self.assertEqual(sql.count('AS PortalShipmentRUB'),2)
        self.assertIn('SUM(PortalShipmentRUB) AS СуммаОтгрузокRUB',sql)
        self.assertIn('s.PriceRUR/IIF(s.Price=0,1,s.Price)',sql)
        self.assertIn('PortalMissingRUB>0',sql)
        self.assertNotIn('AmountRURMotive /',sql)
        self.assertEqual(sql.count('?'),0)


class CurrencyRunTests(TestCase):
    setUp=fixtures.CalculationTests.setUp

    def calculate(self):
        self.config.update(usd_rub_2026='75.5',usd_rub_2027='81.25')
        run=start(self.iteration.pk,self.user,self.config,self.iteration.revision)
        run=execute(run,Mock());self.assertEqual(run.state,'completed',run.error)
        return run

    def test_snapshot_and_excel_keep_both_currencies_without_details(self):
        from .services.coefficient_iteration_export_data import export_snapshot
        from .services.coefficient_iteration_excel import workbook
        run=self.calculate()
        self.assertEqual(run.configuration['usd_rub_2026'],'75.5');self.assertEqual(run.configuration['currency_rule'],RULE)
        for key,value in run.results.items():
            rate=D(run.configuration[f'usd_rub_{value["year"]}'])
            for item in value['rows']:self.assertEqual(D(item['rub']['total']),D(item['total'])*rate)
            self.assertEqual(D(value['plan_fact_summary_rub'][0]['plan']),D(value['plan_fact_summary'][0]['plan'])*rate)
        self.config['usd_rub_2027']='100';run.refresh_from_db()
        self.assertEqual(run.configuration['usd_rub_2027'],'81.25')
        stream=workbook(export_snapshot(run))
        try:
            book=load_workbook(stream);self.assertIn('Получатели 2027 RUB',book.sheetnames)
            self.assertIn('План-факт 2027 RUB',book.sheetnames)
            self.assertNotIn('Источники плана',book.sheetnames)
            sheet=book['Получатели 2027 RUB']
            self.assertIn('RUB',sheet.cell(1,sheet.max_column).value)
            self.assertAlmostEqual(sheet.cell(2,sheet.max_column).value,float(D(run.results['full_2027']['rows'][0]['rub']['total'])))
            book.close()
        finally:stream.close()

    def test_currency_switch_uses_frozen_results_and_legacy_run_is_explicit(self):
        run=self.calculate()
        response=self.client.get(self.url,{'run':run.pk,'currency':'RUB'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Общая выплата, RUB')
        shown=response.context['presentation'][0]['recipients'][0]
        self.assertEqual(D(shown['total']),D(run.results['moscow_q4_2026']['rows'][0]['rub']['total']))
        for value in run.results.values():
            for item in value['rows']:item.pop('rub',None)
        from .models import CoefficientIterationRun
        legacy=CoefficientIterationRun.objects.create(iteration=self.iteration,state='completed',
            inputs=run.inputs,configuration={},results=run.results)
        self.assertContains(self.client.get(self.url,{'run':legacy.pk,'currency':'RUB'}),'рублёвые результаты не сохранены')
        self.assertContains(self.client.get(self.url,{'run':legacy.pk,'currency':'USD'}),'Общая выплата, USD')

    def test_form_requires_rate_only_for_selected_plan(self):
        from .coefficient_iteration_run_views import CalculationForm
        data={**self.config,'revision':self.iteration.revision,'scenarios':['full_2027'],'usd_rub_2027':'','usd_rub_2026':''}
        form=CalculationForm(self.iteration,data);self.assertFalse(form.is_valid())
        self.assertIn('usd_rub_2027',form.errors);self.assertNotIn('usd_rub_2026',form.errors)
        data['usd_rub_2027']='81.25';form=CalculationForm(self.iteration,data)
        self.assertTrue(form.is_valid(),form.errors)

    def test_currency_switch_does_not_load_source_arrays_or_details(self):
        run=self.calculate()
        with patch('admin_motivation.services.coefficient_iteration_export_data.array_records',side_effect=AssertionError('UI must not scan raw source/details')):
            response=self.client.get(self.url,{'run':run.pk,'currency':'RUB'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['run'].get_deferred_fields(),{'inputs','results'})

    def test_running_snapshot_is_not_cached_between_scenario_updates(self):
        from .services.coefficient_iteration_export_data import export_snapshot
        run=self.calculate();run.state='running';run.completed_at=None
        run.save(update_fields=['state','completed_at'])
        first=export_snapshot(run,include_bases=False)
        run.results['full_2027']['rows'][0]['rub']['total']='12345'
        run.save(update_fields=['results'])
        second=export_snapshot(run,include_bases=False)
        self.assertNotEqual(first.results['full_2027']['rows'][0]['rub']['total'],second.results['full_2027']['rows'][0]['rub']['total'])
        self.assertEqual(second.results['full_2027']['rows'][0]['rub']['total'],'12345')
