import sqlite3
from django.test import SimpleTestCase
from .services.motivation_performance import grouped_sql, GLOBAL_PLAN_FACT


class MotivationPerformanceTests(SimpleTestCase):
    def test_global_windows_are_not_multiplied_and_personal_scopes_stay_separate(self):
        fields = ['Подразделение','Год','КварталМотивации','VariationCalculate',
                  'АвторСделки','ГруппаПлановПродаж','ГруппаТоваров','Марка',
                  'СуммаПланUSD','СуммаОтгрузокUSD','ТипКоэф']
        with sqlite3.connect(':memory:') as db:
            db.create_function('LOWER', 1, lambda value: value.lower() if value else value)
            db.execute('CREATE TABLE input (' + ','.join('['+field+']' for field in fields) + ')')
            db.execute('CREATE TABLE plan_fact (Подразделение,Год,ТипКоэф,Квартал,Канал,СуммаПланUSD,СуммаОтгрузокUSD)')
            db.executemany('INSERT INTO plan_fact VALUES (?,?,?,?,?,?,?)', [
                ('A',2026,'Продажи',3,'Dealer',100,75),
                ('A',2026,'Продажи',1,'Dealer',800,525)])
            rows = [
                ('A',2026,'3 Квартал','Подразделение(Глобально)','One','PG1','G','B',100,75),
                ('A',2026,'3 Квартал','Подразделение(Глобально)','Two','PG2','H','C',100,75),
                ('A',2026,'Год','Подразделение(Глобально)','One','PG1','G','B',900,600),
                ('B',2026,'3 Квартал','Группа планов продаж','One','PG1','G','B',100,40),
                ('B',2026,'3 Квартал','Группа планов продаж','One','PG1','H','C',100,40),
                ('B',2026,'3 Квартал','Группа планов продаж','One','PG2','G','B',200,100),
                ('B',2026,'3 Квартал','Группа планов продаж','Two','PG1','G','B',100,80)]
            db.executemany('INSERT INTO input VALUES (?,?,?,?,?,?,?,?,?,?,?)', [(*row,'Политики') for row in rows])
            result = db.execute(grouped_sql('SELECT * FROM input').replace(GLOBAL_PLAN_FACT, 'plan_fact').replace("N'", "'")).fetchall()
        values = {(row[0],row[2],row[4]):row[5:] for row in result}
        self.assertEqual(values[('A','3 Квартал','')], (100,75))
        self.assertEqual(values[('A','Год','')], (900,600))
        self.assertEqual(values[('B','3 Квартал','One')], (300,140))
        self.assertEqual(values[('B','3 Квартал','Two')], (100,80))

    def test_global_report_uses_whole_department_and_same_period_as_payout(self):
        with sqlite3.connect(':memory:') as db:
            db.create_function('LOWER', 1, lambda value: value.lower() if value else value)
            db.execute('CREATE TABLE input (Подразделение,Год,КварталМотивации,VariationCalculate,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка,СуммаПланUSD,СуммаОтгрузокUSD,ТипКоэф)')
            periods = ['1 Квартал','2 Квартал(1-2 кв)','3 Квартал','4 Квартал(1-4 кв)','Год']
            # These projected row maxima suggest 100.32%, but are not totals.
            rows = [('A',2026,period,'Подразделение(Глобально)','Selected','PG','G','B',396570.37,397849.78,'Политики')
                    for period in periods]
            db.executemany('INSERT INTO input VALUES (?,?,?,?,?,?,?,?,?,?,?)', rows * 10)
            db.execute('CREATE TABLE plan_fact (Подразделение,Год,ТипКоэф,Квартал,Канал,СуммаПланUSD,СуммаОтгрузокUSD)')
            db.executemany('INSERT INTO plan_fact VALUES (?,?,?,?,?,?,?)', [
                ('A',2026,'Продажи',1,'Dealer',100,60),
                ('A',2026,'Продажи',2,'Dealer',100,100),
                ('A',2026,'Продажи',3,'Dealer',100,60),
                ('A',2026,'Продажи',3,None,0,6),
                ('A',2026,'Продажи',4,'Dealer',200,100),
                ('A',2026,'Продажи',3,'ДПП',9999,9999),
                ('A',2025,'Продажи',3,'Dealer',9999,9999),
                ('B',2026,'Продажи',3,'Dealer',9999,9999),
                ('A',2026,'Директора',3,'Dealer',200,132),
                ('A',2026,'Политики',3,'Dealer',9999,9999)])
            sql = grouped_sql('SELECT * FROM input WHERE АвторСделки=?')
            sql = sql.replace(GLOBAL_PLAN_FACT, 'plan_fact').replace("N'", "'")
            result = {row[2]: row[5:] for row in db.execute(sql, ['Selected'])}
        self.assertEqual(result, {
            '1 Квартал': (60,60), '2 Квартал(1-2 кв)': (200,160),
            '3 Квартал': (100,66), '4 Квартал(1-4 кв)': (500,326), 'Год': (500,326)})

