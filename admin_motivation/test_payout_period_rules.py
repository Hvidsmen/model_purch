"""Execute production report projections against independent period examples."""
import calendar
import re
import sqlite3
from datetime import date
from pathlib import Path

from django.test import SimpleTestCase

from .services.calculation_variants import full_year_patch
from .services.first_quarter_inputs import patch as first_quarter_patch
from .services.manager_correction import SQL as CORRECTION_SQL
from .services.payout_threshold import parameterize
from .services.pilot_formula import patch_calculation


class PayoutPeriodRuleTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(__file__).parent / 'sql/review/sp_CalcBySub.refactored.sql'
        if not path.exists():
            import unittest
            raise unittest.SkipTest('SQL review source is unavailable')
        sql = full_year_patch(parameterize(first_quarter_patch(patch_calculation(path.read_text()))))
        start = re.search(r'DROP\s+TABLE\s+DataWH\.motivation\.ReportMotivationByDealAuthor\s+SELECT', sql, re.I).end() - len('SELECT')
        end = sql.index('-- Separate 2026 variant:', start)
        cls.query = re.sub(r'INTO\s+DataWH\.motivation\.ReportMotivationByDealAuthor', '', sql[start:end], flags=re.I)
        cls.query = cls.query.replace('#pf_base', 'pf_base').replace("N'", "'")

    def report(self, actuals, quarter_share=.8, year_share=.2):
        fields = ['ТипКоэф','Подразделение','ПодразделениеДокумент','Канал','Год','Квартал',
                  'ГруппаПлановПродаж','ГруппаТоваров','Марка','АвторСделки','KeyPlan',
                  'K0','K1','K2','k3','k4','O0','O1','O2','O3','O4','СуммаМотивацииUSD',
                  'СуммаМотивацииUSDГод','СуммаПланUSD','СуммаОтгрузокUSD','VariationCalculate',
                  'СуммаМотивацииRUR','СуммаМотивацииRURГод']
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE pf_base (' + ','.join('[' + name + ']' for name in fields) + ')')
        for quarter, actual in enumerate(actuals, 1):
            row = dict.fromkeys(fields, 0)
            row.update(ТипКоэф='Политики', Подразделение='A', ПодразделениеДокумент='A', Канал='Dealer',
                       Год=2026, Квартал=quarter, ГруппаПлановПродаж='PG', ГруппаТоваров='G'+str(quarter),
                       Марка='B', АвторСделки='Employee', KeyPlan='AEmployee',
                       VariationCalculate='Подразделение(Глобально)', СуммаПланUSD=100., СуммаОтгрузокUSD=float(actual),
                       СуммаМотивацииUSD=10*quarter*quarter_share, СуммаМотивацииUSDГод=10*quarter*year_share,
                       СуммаМотивацииRUR=1000*quarter*quarter_share, СуммаМотивацииRURГод=1000*quarter*year_share)
            db.execute('INSERT INTO pf_base VALUES (' + ','.join('?' for _ in fields) + ')', [row[name] for name in fields])
        db.execute('CREATE TABLE report AS ' + self.query, {'PayoutThreshold': .8})
        return db

    def paid(self, db):
        return {row[0][0] if row[0] != 'Год' else '5': row[1:] for row in db.execute(
            'SELECT КварталМотивации,SUM(СуммаМотивацииUSDУчетПроцента),SUM(СуммаМотивацииRURУчетПроцента) '
            'FROM report GROUP BY КварталМотивации')}

    def test_q1_is_unconditional_q2_uses_half_year_q3_uses_its_quarter_q4_uses_year(self):
        # Q1 is only 20%; Q2 is 90% cumulatively; Q3 is 90%; year is 82.5%.
        self.assertEqual(self.paid(self.report([20,160,90,60])), {
            '1': (8,800), '2': (16,1600), '3': (24,2400), '4': (32,3200), '5': (20,2000)})

    def test_q2_is_blocked_despite_own_quarter_passing_and_q3_ignores_prior_failure(self):
        result = self.paid(self.report([0,100,90,200]))
        self.assertEqual(result['2'], (0,0))  # Q2=100%, but I-II=50%.
        self.assertEqual(result['3'], (24,2400))
        self.assertEqual(result['4'], (32,3200))

    def test_q4_is_blocked_despite_own_quarter_passing_when_year_fails(self):
        result = self.paid(self.report([0,0,0,200]))
        self.assertEqual(result['4'], (0,0))  # Q4=200%, but full year=50%.
        self.assertEqual(result['5'], (0,0))

    def test_quarter_and_year_shares_remain_separate_and_annual_is_not_q4_only(self):
        result = self.paid(self.report([100,100,100,100], quarter_share=.6, year_share=.4))
        self.assertEqual([result[str(code)][0] for code in range(1,6)], [6,12,18,24,40])

    def test_each_period_selects_its_own_coefficient_at_the_period_end(self):
        db = self.report([20,160,90,60])
        db.create_function('LOWER', 1, lambda value: value.lower() if value else value)
        db.create_function('DATEFROMPARTS', 3, lambda y,m,d: date(y,m,d).isoformat())
        db.create_function('EOMONTH', 1, lambda value: date.fromisoformat(value).replace(day=calendar.monthrange(date.fromisoformat(value).year,date.fromisoformat(value).month)[1]).isoformat())
        db.execute('CREATE TABLE raw_correction (Date_,Subdivision,Kind_,Coeff_)')
        db.executemany('INSERT INTO raw_correction VALUES (?,?,?,?)', [
            ('2026-01-01','A','1 кв.',1.1), ('2026-01-01','A','2 кв.',1.2),
            ('2026-05-01','A','2 кв.',1.25), ('2026-07-01','A','2 кв.',99),
            ('2026-01-01','A','3 кв.',.9), ('2026-01-01','A','4 кв.',.8),
            ('2026-01-01','A','Год',.7), ('2027-01-01','A','Год',99), ('2026-01-01','B','Год',99)])
        # Execute the production kind mapping and dated coefficient lookup.
        mapping = CORRECTION_SQL[CORRECTION_SQL.index('SELECT [Date_]'):CORRECTION_SQL.index('IF EXISTS')]
        mapping = mapping.replace('INTO #portal_manager_correction', '').replace('DataWH.motivation.SubdivisionManagerCoeff', 'raw_correction').replace("N'", "'").strip().rstrip(';')
        db.execute('CREATE TABLE correction AS ' + mapping)
        period = re.search(r'CROSS APPLY \(SELECT (CASE[\s\S]*?END) AS PeriodId\) p', CORRECTION_SQL)[1]
        lookup = re.search(r'OUTER APPLY \((SELECT TOP \(1\) c.Coeff_[\s\S]*?ORDER BY c.Date_ DESC)\) k;', CORRECTION_SQL)[1]
        lookup = lookup.replace('SELECT TOP (1)', 'SELECT').replace('#portal_manager_correction','correction')
        lookup = lookup.replace('p.PeriodId', '(' + period + ')').replace("N'", "'") + ' LIMIT 1'
        db.execute('UPDATE report AS r SET СуммаМотивацииUSDУчетПроцента=r.СуммаМотивацииUSDУчетПроцента*COALESCE((' + lookup + '),1), '
                   'СуммаМотивацииRURУчетПроцента=r.СуммаМотивацииRURУчетПроцента*COALESCE((' + lookup + '),1)')
        expected = {'1':8.8,'2':20,'3':21.6,'4':25.6,'5':14}
        for code, amounts in self.paid(db).items():
            self.assertAlmostEqual(amounts[0], expected[code])
            self.assertAlmostEqual(amounts[1], expected[code]*100)
