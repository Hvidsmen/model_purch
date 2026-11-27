from decimal import Decimal as D
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from .services.premium_forecast import enrich


def premium(code='2',kind='Политики',branch='Д-Питер',possible='100',annual='25'):
    return dict(code=code,kind=kind,subdivision=branch,possible=D(possible),annual_accrual=D(annual),total=D(0),bonus=D(999))


def base(code='2',kind='Политики',branch='Д-Питер',plan='200',fact='100'):
    return dict(period=code+' квартал' if code!='5' else 'Год',kind=kind,subdivision=branch,plan=D(plan),fact=D(fact))


class PremiumForecastTests(SimpleTestCase):
    def display(self,rows,bases):
        display={'period_cards':[{'code':code} for code in dict.fromkeys(r['code'] for r in rows)]}
        ledger=dict(shareholder_forecast=True,rows=rows,coefficient_plan_fact=bases)
        enrich(display,ledger)
        return display

    def test_scales_quarter_and_actual_annual_share_without_bonus(self):
        display=self.display([premium(annual='30')],[base()])
        self.assertEqual(display['forecast_100']['quarter'],D(200))
        self.assertEqual(display['forecast_100']['annual'],D(60))
        self.assertEqual(display['period_cards'][0]['forecast_100']['annual'],D(60))

    def test_each_branch_and_kind_uses_its_own_plan_base(self):
        rows=[premium(),premium(kind='Продажи',annual='10'),premium(branch='Д-Ростов',annual='40')]
        display=self.display(rows,[base(),base(kind='Продажи',plan='50'),base(branch='Д-Ростов',plan='300')])
        self.assertEqual(display['forecast_100']['quarter'],D(550))
        self.assertEqual(display['forecast_100']['annual'],D(175))
        kinds={r['kind']:r for r in display['forecast_100']['kinds']}
        self.assertEqual(kinds['Продажи']['quarter'],D(50))

    def test_year_replaces_annual_contributions_in_total_never_counts_twice(self):
        display=self.display([premium(),premium(code='5',possible='300')],[base(),base(code='5',plan='400')])
        self.assertEqual(display['forecast_100']['quarter'],D(200))
        self.assertEqual(display['forecast_100']['annual'],D(1200))
        self.assertEqual(display['period_cards'][1]['forecast_100']['quarter'],0)

    def test_zero_fact_missing_plan_first_quarter_and_ambiguous_year_are_unknown(self):
        for rows,bases in [([premium()],[base(fact='0')]),([premium()],[base(plan='0')]),([premium()],[]),([premium()],[base(),base()]),([premium(code='1')],[base(code='1')])]:
            display=self.display(rows,bases)
            self.assertIsNone(display['forecast_100']['quarter'])
            self.assertIsNone(display['forecast_100']['annual'])

    def test_partial_unknown_is_not_reported_as_complete_total(self):
        display=self.display([premium(),premium(kind='Продажи')],[base()])
        self.assertIsNone(display['forecast_100']['quarter'])
        self.assertIsNone(display['forecast_100']['annual'])
        self.assertEqual(display['forecast_100']['kinds'][0]['quarter'],D(200))

    def test_zero_premium_is_known_and_ordinary_reports_are_unchanged(self):
        display=self.display([premium(possible='0',annual='0')],[base()])
        self.assertEqual(display['forecast_100']['quarter'],0)
        self.assertEqual(display['forecast_100']['annual'],0)
        untouched={};enrich(untouched,{'rows':[premium()]})
        self.assertEqual(untouched,{})

    def test_summary_and_period_details_render_both_parts(self):
        display=self.display([premium()],[base()])
        for template in ('_team_summary.html','_team_calculation_details.html'):
            html=render_to_string('admin_motivation/reports_next/'+template,dict(presentation=display,ledger={'currency':'USD'},shareholder_report=True))
            self.assertIn('Квартальная часть',html)
            self.assertIn('В годовую премию',html)
            self.assertIn('200,00',html)
            self.assertIn('50,00',html)

    def test_team_presenter_keeps_paid_amounts_and_adds_estimate(self):
        from .services.reports_next_presenter import present
        from .test_quarter_totals import premium as ledger_row
        row=ledger_row('2','Политики','10',branch='Д-Питер')
        row.update(possible=D(100),annual_accrual=D(30))
        ledger=dict(shareholder_forecast=True,rows=[row],total=D(10),kinds=['Политики'],currency='USD',coefficient_plan_fact=[base()])
        display=present('team',ledger,ledger,['2'])
        self.assertEqual(display['total'],D(10))
        self.assertEqual(display['possible'],D(100))
        self.assertEqual(display['forecast_100']['quarter'],D(200))
        self.assertEqual(display['forecast_100']['annual'],D(60))
