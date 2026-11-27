from decimal import Decimal as D
from django.test import SimpleTestCase
from openpyxl import load_workbook
from .services.manager_detail import manager_detail_report,period_motivation_summary
from .services.report_excel import report_excel
from .test_manager_detail import detail_rows


class DetailPeriodColumnsTests(SimpleTestCase):
    def test_second_quarter_alone_has_one_column_in_summary_and_excel(self):
        source=[{**detail_rows()[0],'КварталМотивации':'2 Квартал(1-2 кв)'}]
        for currency,amount in [('USD',D(90)),('RUB',D(9000))]:
            data=manager_detail_report(source,currency)
            summary=data['period_summary']
            self.assertEqual([c['label'] for c in summary['columns']],['2 кв.'])
            for node in [summary['totals'],*summary['groups'],*summary['rows']]:
                self.assertEqual(node['values'],[amount])
                self.assertEqual(node['total'],amount+node['remainder'])
            output=report_excel(data,{},'summary');self.addCleanup(output.close)
            book=load_workbook(output);self.addCleanup(book.close)
            headers=[c.value for c in book['Итого по мотивации'][2]]
            self.assertIn('2 кв.',headers)
            self.assertFalse({'1 кв.','3 кв.','4 кв.','Год'} & set(headers))

    def test_mixed_periods_keep_order_and_missing_employee_period_stays_blank(self):
        one=detail_rows()[0]
        source=[{**one,'КварталМотивации':'3 Квартал'},
                {**one,'КварталМотивации':'Год','АвторСделки':'Другой'},
                {**one,'КварталМотивации':'2 Квартал'}]
        summary=period_motivation_summary(source)
        self.assertEqual([c['label'] for c in summary['columns']],['2 кв.','3 кв.','Год'])
        author=next(r for r in summary['rows'] if r['author']=='Иванов')
        self.assertEqual(author['values'],[D(90),D(90),None])
        self.assertEqual(summary['totals']['values'],[D(90)]*3)

    def test_empty_summary_has_no_fabricated_periods(self):
        summary=period_motivation_summary([])
        self.assertEqual(summary['columns'],[])
        self.assertEqual(summary['totals']['values'],[])
        self.assertEqual(summary['totals']['total'],0)
