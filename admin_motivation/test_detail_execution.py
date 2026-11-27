from copy import deepcopy
from decimal import Decimal as D
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from openpyxl import load_workbook
from .services.detail_execution import enrich
from .services.manager_detail import manager_detail_report
from .services.report_excel import report_excel
from .test_manager_detail import detail_rows


def base(branch='A',plan=100,fact=71,period='2 квартал'):
    return dict(subdivision=branch,year=2026,period=period,plan=D(plan),fact=D(fact))


class DetailExecutionTests(SimpleTestCase):
    def data(self,kinds=('Политики',)):
        source=[{**detail_rows()[0],'КварталМотивации':'2 Квартал'}]
        detail=manager_detail_report(source)
        ledger={'sections':[{'kind':kind,'data':{'_source_rows':source,'coefficient_performance':[base(fact=71)]}} for kind in kinds],
                'department_plan_fact':[base(fact=86),base(fact=86)]}
        return detail,ledger

    def test_department_and_kind_are_distinct_and_personal_metrics_are_unchanged(self):
        detail,ledger=self.data();before=deepcopy(detail['detail_totals'])
        enrich(detail,ledger)
        self.assertEqual([r['label'] for r in detail['execution_rows']],['% выполнения подразделения','% выполнения политиков'])
        department,kind=detail['execution_rows']
        self.assertEqual(department['values'][0][12],D('.86'))
        self.assertIsNone(department['values'][0][16])
        self.assertEqual(kind['values'][0][16],D('.71'))
        self.assertIsNone(kind['values'][0][12])
        self.assertEqual(detail['detail_totals'],before)
        self.assertEqual(department['values'][0],department['values'][-1])
        html=render_to_string('admin_motivation/reports/manager_detail.html',{'data':detail,'new_report_terms':True})
        self.assertIn('% выполнения подразделения',html);self.assertIn('% выполнения политиков',html)
        self.assertIn('86,00%',html);self.assertIn('71,00%',html)

    def test_each_selected_kind_gets_its_own_row_and_excel_matches(self):
        detail,ledger=self.data(('Политики','Продажи','Директора'))
        enrich(detail,ledger)
        labels=[r['label'] for r in detail['execution_rows']]
        self.assertEqual(labels,['% выполнения подразделения','% выполнения политиков','% выполнения продаж','% выполнения директоров'])
        output=report_excel(detail,{},'detail');self.addCleanup(output.close)
        book=load_workbook(output);self.addCleanup(book.close)
        sheet=book['Расширенная мотивация']
        self.assertEqual([sheet.cell(i,1).value for i in range(3,7)],labels)
        self.assertEqual(sheet.cell(3,14).value,.86)
        self.assertEqual(sheet.cell(4,18).value,.71)

    def test_multiple_branches_use_weighted_inputs_and_latest_window(self):
        detail,ledger=self.data()
        detail['periods']=['1 квартал','2 квартал']
        ledger['sections'][0]['data']['_source_rows'].append({'Подразделение':'B','Год':2026})
        ledger['department_plan_fact']=[base(plan=100,fact=20,period='1 квартал'),base(plan=100,fact=90),base(branch='B',plan=300,fact=150)]
        enrich(detail,ledger)
        row=detail['execution_rows'][0]
        self.assertEqual(row['label'],'% выполнения подразделений')
        self.assertEqual(row['values'][1][12],D('.6'))
        self.assertEqual(row['values'][-1][12],D('.6'))
