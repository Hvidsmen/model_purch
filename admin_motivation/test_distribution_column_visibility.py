from django.template.loader import render_to_string
from django.test import SimpleTestCase


class DistributionColumnVisibilityTests(SimpleTestCase):
    def test_distribution_column_is_only_for_sales_and_policies(self):
        for template in ('_team_tables.html','_team_ledger.html'):
            for kind in ('Директора','Кураторы','Продажи','Политики'):
                with self.subTest(template=template,kind=kind):
                    section={'kind':kind,'page':[{'author':'Тест','total':10,'kinds':[kind]}], 'total':10}
                    context={'kind':'team','ledger':{'currency':'USD'},'presentation':{'team_sections':[section],'period_columns':[]}}
                    html=render_to_string('admin_motivation/reports_next/'+template,context)
                    if kind in ('Продажи','Политики'):
                        self.assertIn('После распределения, USD',html)
                        self.assertIn('Не распределено',html)
                    else:
                        self.assertNotIn('После распределения',html)
                        self.assertNotIn('Не распределено',html)

    def test_shareholder_still_has_no_distribution_column(self):
        html=render_to_string('admin_motivation/reports_next/_team_tables.html',{'kind':'team','shareholder_report':True,'ledger':{'currency':'USD'},'presentation':{'team_sections':[{'kind':'Продажи','page':[]}],'period_columns':[]}})
        self.assertNotIn('После распределения',html)
        self.assertIn('colspan="3"',html)
