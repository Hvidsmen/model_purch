from copy import deepcopy
from decimal import Decimal as D
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from .services.detail_summary_breakdown import enrich,catchup_by_author
from .services.shareholder_original_rules import attach
from .test_shareholder_original_rules import row,gate


class DetailSummaryBreakdownTests(SimpleTestCase):
    def data(self):
        employee=dict(subdivision='Политики · Д-Питер',author='One',possible=D(100),values=[D(0)],remainder=D(0),total=D(0))
        group=dict(employee,authors=[deepcopy(employee)])
        detail=dict(currency='USD',period_summary=dict(columns=[dict(label='2 кв.')],totals=dict(employee),groups=[group]))
        ledger=dict(sections=[{},{}],rows=[dict(subdivision='Д-Питер',author='One',kind='Политики',code='2',possible=D(100),annual_accrual=D(25),bonus_quarters={})])
        display=dict(registry_rows=[dict(subdivision='Д-Питер',author='One',kind='Политики',forecast_100=dict(quarter=D(200),annual=D(50)))])
        enrich(detail,ledger,display)
        return detail,ledger,display

    def test_company_type_branch_and_author_match(self):
        detail,_,_=self.data()
        group=detail['period_summary']['groups'][0]
        for node in (detail['period_summary']['totals'],group,group['authors'][0]):
            self.assertEqual(node['quarter_possible'][0]['value'],D(100))
            self.assertEqual([line['label'] for line in node['quarter_possible']],['2 кв.'])
            self.assertEqual(node['quarter_annual'][0]['value'],D(25))
            self.assertEqual(node['annual_accrual'],D(25))
            self.assertEqual(node['forecast_100'],dict(quarter=D(200),annual=D(50)))
            self.assertEqual(sum(x['value'] for x in node['quarter_catchup']),0)

    def test_catchup_breakdown_uses_actual_year_payment_and_preserves_currency_correction(self):
        source=[row('1',100,80),row('2',100,70),row('5',50,50)]
        updated=attach(source,[gate(source[-1],True)])
        self.assertEqual(updated[-1]['CatchupQuartersUSD'],{'1':D(20),'2':D(30)})
        updated[-1]['Остаток']*=2
        breakdown=catchup_by_author(updated,'USD')
        self.assertEqual(breakdown[('Д-Москва','Иванов')],{'1':D(40),'2':D(60),'3':D(0),'4':D(0)})
        blocked=attach(source,[gate(source[-1],False)])
        self.assertEqual(sum(catchup_by_author(blocked,'USD')[('Д-Москва','Иванов')].values()),0)
        self.assertEqual(sum(catchup_by_author(updated,'RUB')[('Д-Москва','Иванов')].values()),D(4500))

    def test_unknown_paid_breakdown_is_not_invented(self):
        source=[{'КварталМотивации':'Год','Подразделение':'A','АвторСделки':'B','Остаток':10}]
        self.assertIsNone(catchup_by_author(source,'USD')[('A','B')])

    def test_template_and_excel_expose_quarters_and_forecast(self):
        detail,_,_=self.data()
        html=render_to_string('admin_motivation/reports/manager_detail.html',dict(data=detail,new_report_terms=True))
        self.assertIn('Прогноз при 100%',html)
        self.assertIn('<details><summary',html)
        from .services.report_excel import report_excel
        from openpyxl import load_workbook
        output=report_excel(detail,{},'summary');self.addCleanup(output.close)
        book=load_workbook(output);self.addCleanup(book.close)
        sheet=book['Итого по мотивации'];headers=[c.value for c in sheet[2]]
        self.assertEqual(sheet.cell(3,headers.index('Возможная премия · 2 кв.')+1).value,100)
        self.assertEqual(sheet.cell(3,headers.index('В годовую премию · 2 кв.')+1).value,25)
        self.assertEqual(sheet.cell(3,headers.index('Прогноз при 100% · квартальная часть')+1).value,200)

    def test_disclosures_only_show_visible_periods_and_keep_zero(self):
        detail,ledger,display=self.data()
        ledger['rows'][0]['possible']=D(0)
        enrich(detail,ledger,display)
        for node in [detail['period_summary']['totals'],detail['period_summary']['groups'][0],detail['period_summary']['groups'][0]['authors'][0]]:
            self.assertEqual(node['quarter_possible'],[{'label':'2 кв.','value':D(0)}])
            self.assertEqual([line['label'] for line in node['quarter_annual']],['2 кв.'])
        detail['period_summary']['columns'].append({'label':'Год'})
        ledger['rows'].append({**ledger['rows'][0],'code':'5','possible':D(20),'annual_accrual':None})
        enrich(detail,ledger,display)
        self.assertEqual(detail['period_summary']['totals']['quarter_possible'],[{'label':'2 кв.','value':D(0)},{'label':'Год','value':D(20)}])
        self.assertEqual([line['label'] for line in detail['period_summary']['totals']['quarter_annual']],['2 кв.'])
