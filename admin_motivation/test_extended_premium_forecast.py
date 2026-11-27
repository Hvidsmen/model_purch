from copy import deepcopy
from decimal import Decimal as D
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from .services.manager_detail import manager_detail_report
from .services.extended_premium_forecast import enrich
from .test_manager_detail import detail_rows


class ExtendedForecastTests(SimpleTestCase):
    def data(self,prefixed=False):
        rows=[]
        for brand,possible in [('A',100),('B',50)]:
            row=deepcopy(detail_rows()[0]);row.update(КварталМотивации='2 Квартал',ТипКоэф='Политики',Марка=brand,Подразделение='Политики · A' if prefixed else 'A',СуммаМотивацииUSD=possible)
            rows.append(row)
        detail=manager_detail_report(rows)
        ledger=dict(currency='USD',sections=[{},{}] if prefixed else [{}],rows=[dict(subdivision='A',author='Иванов',kind='Политики',code='2',possible=D(150),annual_accrual=D(45))],coefficient_plan_fact=[dict(subdivision='A',kind='Политики',period='2 квартал',plan=D(200),fact=D(100))])
        enrich(detail,ledger)
        return detail,ledger

    def test_all_levels_reconcile_and_existing_metrics_stay_intact(self):
        for prefixed in (False,True):
            detail,ledger=self.data(prefixed)
            self.assertEqual(len(detail['headers']),22)
            original=manager_detail_report(detail['_source_rows'])
            self.assertEqual([b[:20] for b in detail['detail_totals']],original['detail_totals'])
            self.assertEqual(detail['detail_totals'][0][-2:],[D(300),D(90)])
            node=detail['nodes'][0]
            while node.get('children'):
                self.assertEqual(node['values'][0][-2:],[D(300),D(90)])
                children=node['children']
                if len(children)>1:
                    self.assertEqual([c['values'][0][-2:] for c in children],[[D(200),D(60)],[D(100),D(30)]])
                    break
                node=children[0]
            self.assertEqual(detail['detail_totals'][0][13],D(150))

    def test_missing_base_produces_unknown_not_zero(self):
        detail,ledger=self.data()
        # Start with a fresh tree: enrichment is a single presentation pass.
        raw=detail['_source_rows'];detail=manager_detail_report(raw)
        ledger['coefficient_plan_fact'][0]['fact']=D(0)
        enrich(detail,ledger)
        self.assertEqual(detail['detail_totals'][0][-2:],[None,None])

    def test_render_and_excel_have_forecast_in_every_period_and_total(self):
        detail,_=self.data()
        html=render_to_string('admin_motivation/reports/manager_detail.html',dict(data=detail,new_report_terms=True))
        self.assertIn('data-metric-count="22"',html)
        self.assertIn('data-forecast-columns="2"',html)
        self.assertIn('--report-columns:8',html)
        from .services.report_excel import report_excel
        from openpyxl import load_workbook
        output=report_excel(detail,{},'detail');self.addCleanup(output.close)
        book=load_workbook(output);self.addCleanup(book.close)
        sheet=book['Расширенная мотивация']
        self.assertEqual(sheet.max_column,45)
        self.assertEqual(sheet.cell(3,22).value,300)
        self.assertEqual(sheet.cell(3,23).value,90)
        self.assertEqual(sheet.cell(3,44).value,300)
        self.assertEqual(sheet.cell(3,45).value,90)
