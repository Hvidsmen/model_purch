from decimal import Decimal as D
from django.http import QueryDict
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from .test_extended_premium_forecast import ExtendedForecastTests
from .services.manager_detail import manager_detail_report
from .services.reports_next_presenter import personal_details


class PersonalMatrixForecastTests(SimpleTestCase):
    def data(self,forecast=True):
        detail,ledger=ExtendedForecastTests().data()
        source=manager_detail_report(detail['_source_rows'])
        ledger.update(shareholder_forecast=forecast,payout_threshold=D('.8'),sections=[{'kind':'Политики','data':source}])
        display=personal_details(ledger,QueryDict('year=2026&period=2'),period_codes=['2'])
        display.update(forecast_100={'quarter':D(300),'annual':D(90)},forecast_100_note='Оценка при прежней структуре продаж')
        return display,ledger

    def test_forecasts_reconcile_from_brands_to_employee_and_total(self):
        display,_=self.data()
        section=display['product_sections'][0]
        self.assertEqual(section['metric_columns'],14)
        self.assertEqual(section['forecast_columns'],2)
        group=section['groups'][0]
        self.assertEqual(group['metrics'][0]['forecast_quarter'],D(300))
        self.assertEqual(group['total_metrics']['forecast_annual'],D(90))
        node=group['children'][0]
        while len(node['children'])==1:node=node['children'][0]
        self.assertEqual(sum(r['metrics'][0]['forecast_quarter'] for r in node['children']),D(300))
        self.assertEqual(sum(r['metrics'][0]['forecast_annual'] for r in node['children']),D(90))

    def test_template_and_ordinary_matrix_column_counts(self):
        display,ledger=self.data()
        html=render_to_string('admin_motivation/reports_next/_my_products.html',dict(presentation=display,ledger=ledger))
        self.assertIn('data-forecast-columns="2"',html)
        self.assertIn('квартальная часть',html)
        self.assertIn('300,00',html)
        old,_=self.data(False)
        self.assertEqual(old['product_sections'][0]['forecast_columns'],0)
        self.assertEqual(old['product_sections'][0]['metric_columns'],10)
