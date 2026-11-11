from decimal import Decimal as D
from django.test import SimpleTestCase
from .services.manager_detail import metrics,manager_detail_report
from .test_manager_detail import detail_rows


class SelectedQuarterFactTests(SimpleTestCase):
    def rows(self):
        return [dict(detail_rows()[0],КварталМотивации=period,
            СуммаОтгрузокUSDКвартал=D(physical),СуммаОтгрузокUSD=D(cumulative))
            for period,physical,cumulative in [('1 Квартал','100','100'),('2 Квартал(1-2 кв)','200','300')]]

    def test_total_uses_physical_quarters_not_cumulative_windows_and_preserves_payout(self):
        rows=self.rows();values=metrics(rows,overall=True)
        self.assertEqual(values[11],300)
        self.assertIsNone(values[10]);self.assertIsNone(values[12])
        self.assertIsNone(values[15]);self.assertIsNone(values[16])
        ordinary=metrics(rows)
        self.assertEqual(values[13],ordinary[13])
        self.assertEqual(values[17:],ordinary[17:])

    def test_returns_zero_single_quarter_and_missing_shipments(self):
        rows=self.rows();rows[1]['СуммаОтгрузокUSDКвартал']=D(-100)
        self.assertEqual(metrics(rows,overall=True)[11],0)
        self.assertEqual(metrics(rows[:1],overall=True)[11],100)
        rows[1]['СуммаОтгрузокUSDКвартал']=None
        self.assertIsNone(metrics(rows,overall=True)[11])

    def test_global_formula_tree_and_department_total_show_selected_fact(self):
        rows=[dict(row,VariationCalculate='Подразделение(Глобально)') for row in self.rows()]
        report=manager_detail_report(rows,performance_rows=rows)
        self.assertEqual(report['detail_totals'][-1][11],300)
        self.assertEqual(report['nodes'][0]['values'][-1][11],300)
        self.assertEqual(report['division_execution'][-1][11],300)
        self.assertIsNone(report['detail_totals'][-1][10])

    def test_incomplete_scope_is_not_misrepresented_as_complete_fact(self):
        rows=self.rows()
        rows.append(dict(rows[0],АвторСделки='Other',КварталМотивации='Год'))
        self.assertIsNone(metrics(rows,overall=True)[11])

    def test_excel_total_contains_the_same_numeric_period_fact(self):
        from openpyxl import load_workbook
        from .services.report_excel import report_excel
        report=manager_detail_report(self.rows())
        output=report_excel(report,{'Currency':'USD'},'all')
        self.addCleanup(output.close)
        book=load_workbook(output);self.addCleanup(book.close)
        sheet=book['Расширенная мотивация']
        headers=[cell.value for cell in sheet[2]]
        column=headers.index('Итого\nФакт периода, USD')
        total=next(row for row in sheet.values if row[0]=='Итого')
        self.assertEqual(total[column],300)
