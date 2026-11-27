from decimal import Decimal as D
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from .services.premium_forecast import enrich
from .services.reports_next_presenter import present
from .test_quarter_totals import premium as ledger_row


def source(author='One',code='2',quarter='200',annual='50',kind='Политики'):
    return {'Подразделение':'Д-Питер','АвторСделки':author,'kind':kind,'code':code,
            'quarter':D(quarter) if quarter is not None else None,'annual':D(annual) if annual is not None else None,'forecast_scope':'Подразделение'}


def row(author='One',code='2',kind='Политики'):
    result=ledger_row(code,kind,'10',author=author,branch='Д-Питер')
    result.update(possible=D(99999),annual_accrual=D(9999))
    return result


def ledger(rows,forecasts):
    return dict(shareholder_forecast=True,plan_forecast_available=True,plan_forecast_rows=forecasts,rows=rows,
                total=sum((r['total'] for r in rows),D(0)),kinds=list(dict.fromkeys(r['kind'] for r in rows)),currency='USD')


class PremiumForecastTests(SimpleTestCase):
    def test_actual_money_and_current_fact_cannot_change_plan_forecast(self):
        data=ledger([row()],[source()]);data['coefficient_plan_fact']=[{'fact':0,'plan':999999}]
        display=present('team',data,data,['2'])
        self.assertEqual(display['forecast_100']['quarter'],D(200))
        self.assertEqual(display['forecast_100']['annual'],D(50))
        self.assertEqual(display['total'],D(10))
        self.assertEqual(display['possible'],D(99999))

    def test_year_replaces_annual_contributions_in_total(self):
        data=ledger([row(),row(code='5')],[source(),source(code='5',quarter='0',annual='400')])
        display=present('team',data,data,['2','5'])
        self.assertEqual(display['forecast_100']['quarter'],D(200))
        self.assertEqual(display['forecast_100']['annual'],D(400))

    def test_missing_snapshot_or_partial_unknown_remains_unknown(self):
        data=ledger([row(),row(kind='Продажи')],[source(),source(kind='Продажи',quarter=None,annual=None)])
        display=present('team',data,data,['2'])
        self.assertIsNone(display['forecast_100']['quarter'])
        self.assertEqual(display['forecast_100']['kinds'][0]['quarter'],D(200))
        data['plan_forecast_rows']=[];data['plan_forecast_available']=False
        display=present('team',data,data,['2'])
        self.assertIsNone(display['forecast_100']['annual'])
        self.assertIn('Создайте новый акционерный расчёт',display['forecast_100_note'])

    def test_first_quarter_forecast_and_zero_rates_are_supported(self):
        data=ledger([row(code='1')],[source(code='1',quarter='0',annual='0')])
        display=present('team',data,data,['1'])
        self.assertEqual(display['forecast_100']['quarter'],0)
        self.assertEqual(display['forecast_100']['annual'],0)
        untouched={};enrich(untouched,{'rows':[]});self.assertEqual(untouched,{})

    def test_employee_kind_and_period_totals_reconcile_and_render(self):
        data=ledger([row(),row(author='Two')],[source(),source(author='Two',quarter='100',annual='25')])
        display=present('team',data,data,['2'])
        section=display['team_sections'][0]
        self.assertEqual([r['forecast_100']['quarter'] for r in section['rows']],[D(200),D(100)])
        self.assertEqual(section['forecast_100']['quarter'],D(300))
        self.assertEqual(section['period_summary_cells'][0]['forecast_100']['annual'],D(75))
        for template in ('_team_summary.html','_team_calculation_details.html','_team_tables.html'):
            html=render_to_string('admin_motivation/reports_next/'+template,dict(presentation=display,ledger=data,kind='team',shareholder_report=True))
            self.assertIn('300,00',html);self.assertIn('75,00',html)

    def test_no_plan_means_zero_forecast_even_with_actual_premium(self):
        data=ledger([row()],[])
        display=present('team',data,data,['2'])
        self.assertEqual(display['forecast_100']['quarter'],0)
        self.assertEqual(display['forecast_100']['annual'],0)
