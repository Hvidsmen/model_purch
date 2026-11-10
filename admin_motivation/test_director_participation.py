from pathlib import Path
import re
import sqlite3

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from .services.director_participation import EXCLUDED, MARKER, patch
from .services.motivation_performance import GLOBAL_PLAN_FACT, grouped_sql


SOURCE = """DELETE FROM #pf_base WHERE Канал = N'ДПП';
DELETE FROM #pf_base
WHERE Подразделение IN ('ДПП','REtail','E-Com','СНГ','Д-Маврикий','Буфер','Брендинг');
"""


class DirectorParticipationTests(SimpleTestCase):
    def test_directors_survive_both_filters_while_other_types_keep_exclusions(self):
        sql = patch(SOURCE).replace('#pf_base', 'base').replace("N'", "'")
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE base (Подразделение,ТипКоэф,Канал)')
            rows = [(branch, kind, channel) for branch in (*EXCLUDED, 'Д-Москва')
                    for kind in ('Директора', 'Политики', 'Продажи', None)
                    for channel in ('Dealer', 'ДПП')]
            rows.append(('REtail', '  Директора  ', 'Dealer'))
            db.executemany('INSERT INTO base VALUES (?,?,?)', rows)
            db.executescript(sql)
            actual = db.execute('SELECT * FROM base').fetchall()
        expected = [(branch, 'Директора', channel) for branch in (*EXCLUDED, 'Д-Москва') for channel in ('Dealer', 'ДПП')]
        expected += [('Д-Москва', kind, 'Dealer') for kind in ('Политики', 'Продажи', None)]
        expected.append(('REtail', '  Директора  ', 'Dealer'))
        self.assertCountEqual(actual, expected)

    def test_patch_supports_original_and_refactored_procedures(self):
        root = Path(__file__).parent / 'sql' / 'review'
        for name in ('sp_CalcBySub.original.sql', 'sp_CalcBySub.refactored.sql'):
            with self.subTest(name=name):
                original = (root / name).read_text()
                updated = patch(original)
                self.assertEqual(updated.count(MARKER), 1)
                self.assertEqual(updated.count("COALESCE(LTRIM(RTRIM([ТипКоэф])),N'')<>N'Директора'"), 2)
                self.assertEqual(patch(updated), updated)
                channel = re.search(r"DELETE FROM #pf_base\s+WHERE\s+Канал ='ДПП'", original)[0]
                self.assertIn(channel + " AND COALESCE(LTRIM(RTRIM([ТипКоэф])),N'')<>N'Директора'", updated)

    def test_unknown_or_ambiguous_filters_fail_before_calculation(self):
        for source in (SOURCE.replace("'Брендинг'", "'Другой'"), SOURCE + SOURCE,
                       SOURCE.replace("Канал = N'ДПП'", "Канал = N'Другой'"),
                       SOURCE.replace("Канал = N'ДПП'", "Канал = N'ДПП' OR Год=2025"),
                       'SELECT * INTO #pf_base FROM input; DELETE FROM #pf_base WHERE Подразделение=@name;'):
            with self.subTest(source=source), self.assertRaises(ValidationError):
                patch(source)

    def test_comments_do_not_supply_a_fake_filter(self):
        with self.assertRaises(ValidationError):
            patch('SELECT * FROM #pf_base;\n/* ' + SOURCE + ' */')

    def test_report_uses_same_director_scope_for_quarters_year_and_total(self):
        with sqlite3.connect(':memory:') as db:
            db.create_function('LOWER', 1, lambda value: value.lower() if value else value)
            db.execute('CREATE TABLE input (Подразделение,Год,КварталМотивации,VariationCalculate,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка,СуммаПланUSD,СуммаОтгрузокUSD)')
            periods = ['1 Квартал', '2 Квартал(1-2 кв)', '3 Квартал', '4 Квартал(1-4 кв)', 'Год']
            db.executemany('INSERT INTO input VALUES (?,?,?,?,?,?,?,?,?,?)', [
                (branch, 2026, period, 'Подразделение(Глобально)', 'Director', 'PG', 'G', 'B', 999, 999)
                for branch in EXCLUDED for period in periods])
            db.execute('CREATE TABLE plan_fact (Подразделение,Год,ТипКоэф,Квартал,Канал,СуммаПланUSD,СуммаОтгрузокUSD)')
            db.executemany('INSERT INTO plan_fact VALUES (?,?,?,?,?,?,?)', [
                (branch, 2026, kind, quarter, channel, plan, fact)
                for branch in EXCLUDED for quarter in (1, 2, 3, 4)
                for kind, channel, plan, fact in [('Продажи', 'Dealer', 100, 90),
                    ('Продажи', 'ДПП', 9999, 9999), ('Директора', 'Dealer', 9999, 9999)]])
            db.execute("ALTER TABLE plan_fact ADD COLUMN ГруппаПлановПродаж DEFAULT '1. RAC'")
            db.create_function('PATINDEX', 2, lambda pattern, value: next((i + 1 for i, c in enumerate(value or '') if c not in '0123456789. '), 0))
            for kind in ('Директора', 'Продажи', 'Политики'):
                sql = grouped_sql('SELECT * FROM input', coefficient_type=kind)
                sql = sql.replace(GLOBAL_PLAN_FACT, 'plan_fact').replace("N'", "'")
                result = db.execute(sql).fetchall()
                if kind != 'Директора':
                    self.assertEqual(result, [])
                    continue
                values = {(row[0], row[2]): row[5:] for row in result}
                for branch in EXCLUDED:
                    self.assertEqual(values[branch, '1 Квартал'], (10089, 10089))
                    self.assertEqual(values[branch, '2 Квартал(1-2 кв)'], (20198, 20178))
                    self.assertEqual(values[branch, '3 Квартал'], (10099, 10089))
                    # The existing 2026 transition uses Q4 inputs for Q4 and annual eligibility.
                    self.assertEqual(values[branch, '4 Квартал(1-4 кв)'], (10099, 10089))
                    self.assertEqual(values[branch, 'Год'], (10099, 10089))
                    self.assertEqual(values[branch, 'Итого'], (40396, 40356))

