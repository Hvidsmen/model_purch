import sqlite3
from django.test import SimpleTestCase
from .services.motivation_performance import grouped_sql


class MotivationPerformanceTests(SimpleTestCase):
    def test_global_windows_are_not_multiplied_and_personal_scopes_stay_separate(self):
        fields = ['Подразделение','Год','КварталМотивации','VariationCalculate',
                  'АвторСделки','ГруппаПлановПродаж','ГруппаТоваров','Марка',
                  'СуммаПланUSD','СуммаОтгрузокUSD']
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE input (' + ','.join('['+field+']' for field in fields) + ')')
            rows = [
                ('A',2026,'3 Квартал','Подразделение(Глобально)','One','PG1','G','B',100,75),
                ('A',2026,'3 Квартал','Подразделение(Глобально)','Two','PG2','H','C',100,75),
                ('A',2026,'Год','Подразделение(Глобально)','One','PG1','G','B',900,600),
                ('B',2026,'3 Квартал','Группа планов продаж','One','PG1','G','B',100,40),
                ('B',2026,'3 Квартал','Группа планов продаж','One','PG1','H','C',100,40),
                ('B',2026,'3 Квартал','Группа планов продаж','One','PG2','G','B',200,100),
                ('B',2026,'3 Квартал','Группа планов продаж','Two','PG1','G','B',100,80)]
            db.executemany('INSERT INTO input VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
            result = db.execute(grouped_sql('SELECT * FROM input').replace("N'", "'")).fetchall()
        values = {(row[0],row[2],row[4]):row[5:] for row in result}
        self.assertEqual(values[('A','3 Квартал','')], (100,75))
        self.assertEqual(values[('A','Год','')], (900,600))
        self.assertEqual(values[('B','3 Квартал','One')], (300,140))
        self.assertEqual(values[('B','3 Квартал','Two')], (100,80))
