import sqlite3
from datetime import date
from decimal import Decimal
from django.test import SimpleTestCase
from .services.deal_amounts import QUARTERLY_SQL, ANNUAL_SQL, POSSIBLE_SQL, detail_projection
from .services.deal_presentation import COLUMNS, prepare, page_csv
from django.http import QueryDict


class DealAmountsTests(SimpleTestCase):
    def test_quarter_boundaries_totals_details_and_csv_use_same_amounts(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.create_function('MONTH', 1, lambda value: date.fromisoformat(value[:10]).month if value else None)
        numeric = {'СуммаОтгрузокUSD', 'СуммаКвМотивацииUSD', 'СуммаМотивацииUSDГод'}
        connection.execute('CREATE TABLE deals (' + ','.join(f'[{key}] ' + ('NUMERIC' if key in numeric else 'TEXT') for key, _, _ in COLUMNS) + ')')
        for day, quarterly, annual in [('2026-01-01', 80, 20), ('2026-03-31', 80, 20), ('2026-09-30', 80, 20), ('2026-10-01', 80, 20), ('2026-12-31', 80, 20), ('2026-10-02', -8, -2), ('2026-02-01', None, 5)]:
            connection.execute('INSERT INTO deals ([ДатаЗакрытияСделки],[СуммаКвМотивацииUSD],[СуммаМотивацииUSDГод]) VALUES (?,?,?)', (day, quarterly, annual))
        summary = connection.execute(f'SELECT SUM({QUARTERLY_SQL}),SUM({ANNUAL_SQL}),SUM({POSSIBLE_SQL}) FROM deals').fetchone()
        self.assertEqual(summary, (152, 103, 255))
        cursor = connection.execute('SELECT ' + detail_projection() + ' FROM deals ORDER BY [ДатаЗакрытияСделки]')
        rows = [dict(zip([column[0] for column in cursor.description], values)) for values in cursor.fetchall()]
        amounts = {row['ДатаЗакрытияСделки']: (row['СуммаКвМотивацииUSD'], row['СуммаМотивацииUSDГод'], row['СуммаМотивацииUSD']) for row in rows}
        self.assertEqual(amounts['2026-09-30'], (0, 20, 20))
        self.assertEqual(amounts['2026-10-01'], (80, 20, 100))
        self.assertEqual(amounts['2026-10-02'], (-8, -2, -10))
        self.assertEqual(amounts['2026-02-01'], (0, 5, 5))
        self.assertEqual(sum(row['СуммаМотивацииUSD'] for row in rows), summary[2])
        data = prepare({'details': rows, 'offset': 0, 'page': 1}, QueryDict())
        import csv, io
        exported = list(csv.reader(io.StringIO(page_csv(data).content.decode('utf-8-sig')), delimiter=';'))
        quarterly_index = exported[0].index('Возможная квартальная мотивация, USD')
        annual_index = exported[0].index('Возможная годовая мотивация, USD')
        self.assertEqual(sum(Decimal(row[quarterly_index]) for row in exported[1:]), Decimal(summary[0]))
        self.assertEqual(sum(Decimal(row[annual_index]) for row in exported[1:]), Decimal(summary[1]))
