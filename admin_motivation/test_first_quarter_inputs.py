import sqlite3
from decimal import Decimal
from django.test import SimpleTestCase
from .services.first_quarter_inputs import normalize, patch
from .services.manager_detail import manager_detail_report
from .services.pilot_formula import GLOBAL_SUBDIVISION
from .test_manager_detail import detail_rows


class FirstQuarterInputsTests(SimpleTestCase):
    def test_q1_uses_period_fact_without_mutating_source_or_payments(self):
        rows = [dict(row, VariationCalculate=GLOBAL_SUBDIVISION) for row in detail_rows()]
        performance = [dict(row, СуммаОтгрузокUSDКвартал=6469139, СуммаПланUSD=235214543,
                            СуммаОтгрузокUSD=235214543) for row in rows]
        data = manager_detail_report(rows, performance_rows=performance)
        self.assertEqual(data['detail_totals'][0][14:17], [Decimal(6469139), Decimal(6469139), Decimal(1)])
        self.assertEqual(data['division_execution'][0][14:17], [Decimal(6469139), Decimal(6469139), Decimal(1)])
        self.assertEqual(data['detail_totals'][0][11], 800)  # Own period shipments remain own.
        self.assertEqual(data['detail_totals'][0][17:], [90, 5, 95])
        self.assertEqual(data['detail_totals'][1][14], Decimal(235214543))  # Year unchanged.
        self.assertEqual(performance[0]['СуммаПланUSD'], 235214543)
        for branch in data['nodes']:
            self.assertEqual(branch['values'][0][14:17], [Decimal(6469139), Decimal(6469139), Decimal(1)])
        zero = normalize([dict(rows[0], СуммаОтгрузокUSDКвартал=0)])[0]
        self.assertEqual(zero['СуммаПланUSD'], 0)

    def test_sql_patch_removes_repeated_window_totals_only_in_q1_output(self):
        projection = "SUM(СуммаОтгрузокUSD) OVER(PARTITION BY KeyPlan,ТипКоэф) СуммаОтгрузокUSD, SUM(СуммаОтгрузокUSD) OVER(PARTITION BY KeyPlan,ТипКоэф) СуммаПланUSD, '1 Квартал' КварталМотивации"
        source = 'SELECT ' + projection + ' FROM inputs WHERE Квартал=1'
        fixed = patch(source)
        self.assertNotIn('OVER', fixed)
        self.assertEqual(patch(fixed), fixed)
        self.assertEqual(patch('-- ' + source), '-- ' + source)
        later = source.replace('1 Квартал', '2 Квартал(1-2 кв)')
        self.assertEqual(patch(later), later)
        db = sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE inputs(KeyPlan text,ТипКоэф text,СуммаОтгрузокUSD real,Квартал int)')
            db.executemany('INSERT INTO inputs VALUES (?,?,?,?)', [('A','Политики',10,1), ('A','Политики',30,1)])
            self.assertEqual(sum(row[0] for row in db.execute(source)), 80)
            self.assertEqual(sum(row[0] for row in db.execute(fixed)), 40)
        finally:
            db.close()
