from copy import deepcopy
from decimal import Decimal as D
from django.http import QueryDict
from django.test import SimpleTestCase,TestCase
from .services.plan_premium_forecast import History,estimate,load,plan_sql,HISTORY_SQL,VERSION
from .services.premium_forecast import enrich


def plan(**changes):
    return {'Подразделение':'A','АвторСделки':'One','ГруппаПлановПродаж':'RAC','ГруппаТоваров':'G','Марка':'B',
            'Год':2026,'Квартал':1,'ДатаЗакрытияСделки':'2026-01-01','ТипКоэф':'Политики',
            'СуммаПланUSD':'1000',**{f'K{i}':str(D(i+1)/100) for i in range(5)},**changes}


def sale(branch='A',brand='B',segment=0,usd=100):
    return {'Подразделение':branch,'ГруппаПлановПродаж':'RAC','ГруппаТоваров':'G','Марка':brand,'Segment':segment,'USD':usd}


class PlanForecastTests(SimpleTestCase):
    def test_weighted_plan_uses_no_current_premium_or_fact_and_reconciles(self):
        history=History([sale(segment=0,usd=25),sale(segment=2,usd=75)])
        row=plan();before=deepcopy(row)
        result=estimate(row,history,D('.8'),D('.2'))
        self.assertEqual((result['quarter'],result['annual']),(D(20),D(5)))
        self.assertEqual(sum(r['plan'] for r in result['forecast_parts']),D(1000))
        self.assertEqual(result['forecast_scope'],'Товар')
        row.update(СуммаОтгрузокUSD=0,СуммаМотивацииUSD=99999999)
        self.assertEqual(estimate(row,history,D('.8'),D('.2'))['quarter'],D(20))
        self.assertEqual(before['СуммаПланUSD'],row['СуммаПланUSD'])

    def test_fallback_product_then_branch_then_company(self):
        history=History([sale(segment=0,usd=100),sale(brand='Other',segment=4,usd=300),sale(branch='X',segment=1,usd=400)])
        for row,scope,premium in [(plan(),'Товар',10),(plan(Марка='Unknown'),'Подразделение',40),(plan(Подразделение='New'),'Компания',30)]:
            result=estimate(row,history,D(1),D(0))
            self.assertEqual((result['forecast_scope'],result['quarter']),(scope,D(premium)))

    def test_returns_do_not_create_negative_or_exploding_weights(self):
        history=History([sale(usd=100),sale(segment=4,usd=-99)])
        self.assertEqual(estimate(plan(),history,D(1),D(0))['quarter'],D(10))

    def test_unknown_history_or_required_rate_is_not_zero_or_k0(self):
        for row,history in [(plan(),History([])),(plan(K4=None),History([sale(segment=4)]))]:
            self.assertIsNone(estimate(row,history,D(1),D(0))['quarter'])
        result=estimate(plan(K4=None),History([sale()]),D(1),D(0))
        self.assertEqual(result['quarter'],D(10))

    def test_zero_plan_and_zero_rates_are_known(self):
        self.assertEqual(estimate(plan(СуммаПланUSD=0),History([]),D(1),D(0))['quarter'],0)
        self.assertEqual(estimate(plan(K0=0),History([sale()]),D(1),D(0))['quarter'],0)

    def test_plan_fx_only(self):
        history=History([sale()])
        self.assertEqual(estimate(plan(),history,D('.8'),D('.2'),D(90))['quarter'],D(720))
        self.assertIsNone(estimate(plan(),history,D('.8'),D('.2'),None)['quarter'])

    def test_sql_reads_only_2025_and_native_plan_rates_not_report_weighted_rates(self):
        for table in ('#plan','#plan_2024'):
            sql=plan_sql('SELECT p.* INTO '+table+' FROM source p;','Политики')
            self.assertIn('FROM '+table,sql);self.assertIn('[K4]',sql);self.assertIn('[Год]=2026',sql)
        self.assertIn("'20250101'",HISTORY_SQL);self.assertIn("'20260101'",HISTORY_SQL)
        self.assertNotIn('GETDATE',HISTORY_SQL)


class PlanForecastSnapshotTests(TestCase):
    def setUp(self):
        from .test_shareholder_original_rules import OriginalSnapshotTests
        from .models import ShareholderSnapshot
        OriginalSnapshotTests.setUp(self)
        self.run.metadata['plan_forecast_version']=VERSION
        self.run.configuration.update(quarter_share='80',year_share='20',forecast_usd_rub='90')
        self.run.save(update_fields=['metadata','configuration'])
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='forecast_history',sequence=0,payload=[sale(segment=0,usd=25),sale(segment=2,usd=75)])
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='forecast_plan_pol',sequence=0,payload=[plan(),plan(Квартал=2,Марка='Other',СуммаПланUSD='2000')])

    def test_q1_and_year_use_plan_and_year_counts_all_quarters_once(self):
        rows=load(self.run,QueryDict('currency=USD'),['1','5'],['Политики'])
        self.assertEqual(sum(r['quarter'] for r in rows),D(20))
        self.assertEqual(sum(r['annual'] for r in rows if r['code']=='5'),D(15))
        self.assertEqual({r['forecast_scope'] for r in rows},{'Товар','Подразделение'})

    def test_filters_and_saved_rub_fx_and_no_legacy_extrapolation(self):
        rows=load(self.run,QueryDict('currency=RUB&brand=B'),['1'],['Политики'])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['quarter'],D(1800))
        self.run.metadata={}
        self.assertEqual(load(self.run,QueryDict(),['1'],['Политики']),[])

    def test_separate_quarters_and_combined_annual_are_not_mixed(self):
        from .models import ShareholderSnapshot
        self.run.scenario='separate'
        ShareholderSnapshot.objects.create(calculation=self.run,mode='separate',dataset='forecast_plan_pol',sequence=0,payload=[plan(СуммаПланUSD='4000')])
        rows=load(self.run,QueryDict(),['1','5'],['Политики'])
        self.assertEqual(sum(r['quarter'] for r in rows),D(80))
        self.assertEqual(sum(r['annual'] for r in rows if r['code']=='5'),D(15))

    def test_report_integration_and_excel_explain_fallback(self):
        from .services import shareholder_reports
        from .services.reports_next_presenter import present
        from .services.presentation_excel import workbook
        from .models import ShareholderSnapshot
        from openpyxl import load_workbook
        ShareholderSnapshot.objects.filter(calculation=self.run,dataset='forecast_plan_pol').update(payload=[plan(Подразделение='Д-Москва',АвторСделки='Иванов',ГруппаПлановПродаж='1. RAC',ГруппаТоваров='Бытовые кондиционеры',Марка='A')])
        query=QueryDict('type=Политики&subdivision=Д-Москва&period=1')
        data=shareholder_reports.data_for(self.run,query,detailed=False)
        display=present('team',data,data,['1'])
        self.assertEqual(display['forecast_100']['quarter'],D(20))
        self.assertEqual(display['forecast_100']['annual'],D(5))
        self.assertEqual(data['total'],D(100))
        output=workbook('team',data,[],query);self.addCleanup(output.close)
        book=load_workbook(output);self.addCleanup(book.close)
        values=list(book['Прогноз от плана'].values)
        self.assertEqual(values[1][8],'Компания')
        self.assertEqual(sum(D(str(r[11])) for r in values[1:]),D(1000))
        self.assertEqual(sum(D(str(r[13])) for r in values[1:]),D(25))

    def test_combined_moscow_history_includes_thermotrade(self):
        from .models import ShareholderSnapshot
        ShareholderSnapshot.objects.filter(calculation=self.run,dataset='forecast_history').update(payload=[sale(branch='Д-Москва',segment=0),sale(branch='ТЕРМОТРЕЙД',segment=4)])
        ShareholderSnapshot.objects.filter(calculation=self.run,dataset='forecast_plan_pol').update(payload=[plan(Подразделение='Д-Москва')])
        combined=load(self.run,QueryDict(),['1'],['Политики'])
        self.assertEqual(combined[0]['quarter'],D(24))

    def test_missing_planned_fx_does_not_substitute_current_fact_rate(self):
        self.run.configuration.pop('forecast_usd_rub')
        rows=load(self.run,QueryDict('currency=RUB'),['1'],['Политики'])
        self.assertIsNone(rows[0]['quarter'])

    def test_planned_fx_input_validation(self):
        from .shareholder_forms import CalculationForm
        from django.core.exceptions import ValidationError
        field=CalculationForm.base_fields['forecast_usd_rub']
        self.assertEqual(field.clean('95.25'),D('95.25'))
        self.assertIsNone(field.clean(''))
        with self.assertRaises(ValidationError):field.clean('0')
        with self.assertRaises(ValidationError):field.clean('-1')

    def test_native_installed_batch_exposes_plan_rates_for_forecast(self):
        from .test_shareholder_installed_sql import InstalledRegionalSQLTests
        batch=InstalledRegionalSQLTests.compiled(self)
        sql=plan_sql(batch,'Директора')
        self.assertIn('FROM #plan_2024',sql)
        self.assertIn('[K0],[K1],[K2],[K3],[K4]',sql)
