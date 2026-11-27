from copy import deepcopy
from decimal import Decimal as D
from io import BytesIO
from django.core.paginator import Paginator
from django.http import QueryDict
from django.template.loader import get_template,render_to_string
from django.test import SimpleTestCase
from openpyxl import load_workbook
from .test_quarter_totals import premium
from .services.reports_next_presenter import present
from .services.presentation_excel import workbook


class CatchupVisibilityTests(SimpleTestCase):
    def data(self,periods):
        rows=[premium(code,'Политики','10') for code in periods]
        for r in rows:r.update(potential_bonus=D(5),annual_accrual=D(2),currency='USD')
        data=dict(rows=rows,total=sum((r['total'] for r in rows),D(0)),currency='USD',kinds=['Политики'],original_rules=True,shareholder_forecast=True)
        display=present('team',data,data,periods)
        for section in display['team_sections']:section['page']=Paginator(section['rows'],50).page(1)
        return data,display

    def test_year_selection_controls_all_shared_cards_and_tables(self):
        for periods in (['2'],['1','2','3','4'],['5'],['2','5']):
            data,display=self.data(periods);show='5' in periods
            self.assertEqual(display['show_catchup'],show)
            context=dict(presentation=display,ledger=data,shareholder_report=True,shareholder_original_rules=True,kind='team')
            for template in ('_team_summary.html','_team_tables.html','_team_calculation_details.html','_my_periods.html'):
                with self.subTest(periods=periods,template=template):
                    html=render_to_string('admin_motivation/reports_next/'+template,context)
                    if not show:
                        self.assertNotIn('Возможная доплата за кварталы',html)
                        self.assertNotIn('>Доплата за кварталы',html)
                        self.assertNotIn('>Годовая доплата за кварталы',html)
                    if template=='_team_tables.html':self.assertEqual('>Доплата за кварталы, USD</th>' in html,show)

    def test_presentation_excel_removes_columns_and_values_without_changing_totals(self):
        for periods in (['2'],['5'],['2','5']):
            data,_=self.data(periods);before=deepcopy(data)
            output=workbook('team',data,[],QueryDict('&'.join('period='+c for c in periods)));self.addCleanup(output.close)
            book=load_workbook(output);self.addCleanup(book.close)
            for sheet in book:
                if sheet.title=='Параметры':continue
                headers=[c.value for c in sheet[5]]
                if '5' not in periods:self.assertFalse(any('доплат' in str(h).casefold() for h in headers),(sheet.title,headers))
            self.assertEqual(data,before)

    def test_native_detail_and_excel_hide_catchup_without_year(self):
        from .services.manager_detail import manager_detail_report
        from .services.report_excel import report_excel
        from .test_manager_detail import detail_rows
        rows=[{**r,'КварталМотивации':'2 Квартал'} for r in detail_rows()]
        data=manager_detail_report(rows)
        self.assertFalse(data['show_catchup'])
        html=render_to_string('admin_motivation/reports/manager_detail.html',{'data':data,'new_report_terms':True})
        self.assertNotIn('class="summary-remainder"',html)
        self.assertIn('data-show-catchup="false"',html)
        output=report_excel(data,{},'all');self.addCleanup(output.close)
        book=load_workbook(output);self.addCleanup(book.close)
        for sheet in book:
            self.assertFalse(any('доплат' in str(cell.value).casefold() for cell in sheet[2]),sheet.title)

    def test_changed_templates_compile(self):
        for name in ('_ledger','_team_ledger','_employee_pivot_row','_subdivision_summary','_my_products','_my_matrix_values','_shareholder_executive_cards'):
            get_template('admin_motivation/reports_next/'+name+'.html')

    def test_plain_personal_excel_and_distributed_sheet_follow_selection(self):
        from .reports_next_views import workbook as excel
        from .services.distribution_reporting import excel_sheet
        from openpyxl import Workbook
        for periods in (['2'],['5']):
            data,_=self.data(periods)
            query=QueryDict('&'.join('period='+c for c in periods))
            output=excel('mine',data,[],query=query)
            self.addCleanup(output.close)
            book=load_workbook(output);self.addCleanup(book.close)
            headers=[c.value for c in book['Премии'][2]]
            self.assertEqual(any('доплат' in str(h).casefold() for h in headers),'5' in periods)
            rows=[{**r,'distributed_total':D(10),'distributed':D(10),'distributed_bonus':D(0)} for r in data['rows']]
            native=Workbook(write_only=True);excel_sheet(native,rows)
            buffer=BytesIO();native.save(buffer);native.close();buffer.seek(0)
            allocated=load_workbook(buffer);self.addCleanup(allocated.close)
            self.assertEqual(any('Доплата' in str(c.value) for c in allocated['После распределения'][1]),'5' in periods)
