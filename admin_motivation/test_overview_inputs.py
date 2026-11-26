import re
import sqlite3
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from .services import overview_inputs as inputs, manager_detail, motivation_performance


class OverviewInputTests(SimpleTestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        fields = [*manager_detail.DIMENSIONS, 'Год', 'КварталМотивации', 'VariationCalculate', 'ТипКоэф',
                  *manager_detail.MONEY_FIELDS, 'СуммаПланUSD', 'СуммаОтгрузокUSD']
        self.db.execute('CREATE TABLE raw (' + ','.join('['+f+']' for f in fields) + ')')
        for formula in ('Подразделение(Глобально)', 'Марка(Бренд)', 'Группа товаров', 'Группа планов продаж'):
            for brand in ('A', 'B'):
                for _ in range(2):
                    row = ['Д-Москва', 'Иван', 'RAC', 'Goods', brand, 2026, '2 квартал', formula, 'Политики',
                           10, 8, 2, 1000, 800, 200, 100, 80]
                    self.db.execute('INSERT INTO raw VALUES ('+','.join('?' for _ in row)+')', row)

    @staticmethod
    def sql(sql):
        return re.sub(r"\bN'", "'", sql).replace(inputs.REPORT, 'compact').replace(inputs.PLAN, 'plan_compact')

    def stage(self):
        sql = self.sql(inputs.report_sql('SELECT * FROM raw'))
        self.db.execute('CREATE TABLE compact AS '+sql.replace(' INTO compact', ''))

    def rows(self, sql):
        return sorted(self.db.execute(self.sql(sql)).fetchall(), key=repr)

    def test_compaction_preserves_all_money_and_personal_execution_windows(self):
        self.stage()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM compact').fetchone()[0], 8)
        self.assertEqual(self.rows(manager_detail.grouped_sql('SELECT * FROM raw', summary_only=True)),
                         self.rows(manager_detail.grouped_sql('SELECT * FROM compact', summary_only=True)))
        # Personal windows use MAX by formula-specific product dimensions, then SUM.
        for formula, keys in [('Марка(Бренд)', '[Марка]'), ('Группа товаров', '[ГруппаТоваров]'),
                              ('Группа планов продаж', '[ГруппаПлановПродаж]')]:
            query = "SELECT SUM(p),SUM(f) FROM (SELECT MAX([СуммаПланUSD]) p,MAX([СуммаОтгрузокUSD]) f FROM {} WHERE [VariationCalculate]=? GROUP BY "+keys+')'
            self.assertEqual(self.db.execute(query.format('raw'), (formula,)).fetchone(),
                             self.db.execute(query.format('compact'), (formula,)).fetchone())

    def test_plan_staging_retains_unselected_products_and_sales_department_base(self):
        self.stage()
        fields = ['Подразделение','Год','Квартал','ТипКоэф','ГруппаПлановПродаж','ГруппаТоваров','Марка','Канал','СуммаПланUSD','СуммаОтгрузокUSD']
        self.db.execute('CREATE TABLE plan ('+','.join('['+f+']' for f in fields)+')')
        for kind in ('Политики', 'Продажи', 'Директора'):
            for quarter in range(1, 5):
                for branch in ('Д-Москва', 'Д-Ростов'):
                    row = [branch, 2026, quarter, kind, 'Unselected', 'Other', 'Other', 'DPP', 100, 90]
                    self.db.execute('INSERT INTO plan VALUES ('+','.join('?' for _ in row)+')', row)
        sql = self.sql(inputs.plan_sql('main')).replace('DataWH.motivation.PlanFact', 'plan')
        self.db.execute('CREATE TABLE plan_compact AS '+sql.replace('INTO plan_compact ', ''), ('Политики',))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM plan_compact').fetchone()[0], 4)
        before = motivation_performance.department_sql('SELECT * FROM raw', 'main').replace('DataWH.motivation.PlanFact', 'plan')
        after = motivation_performance.department_sql('SELECT * FROM compact', 'main').replace('DataWH.motivation.PlanFact', 'plan_compact')
        self.assertEqual(self.rows(before), self.rows(after))

    def test_prepare_reads_large_sources_once_and_keeps_parameters_bound(self):
        cursor = Mock()
        self.assertEqual(inputs.prepare(cursor, 'SELECT * FROM big WHERE author=?', ['Иван'], 'Политики', 'main'),
                         'SELECT * FROM '+inputs.REPORT)
        calls = cursor.execute.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[2].args[1:], ('Иван',))
        self.assertEqual(calls[3].args[1:], ('Политики',))
        self.assertEqual(sum(call.args[0].count('FROM big') for call in calls), 1)

    def test_author_cohort_uses_paid_quarter_and_product_filters(self):
        sql = inputs.author_sql(['2'], 'all_2026')
        self.assertIn('[Квартал] IN (2)', sql)
        self.assertIn('FactAll2026', sql)
        for param in ('@Subdivision', '@Years', '@TypeCoeff_', '@gp_sales', '@GPGoods', '@Brend'):
            self.assertIn(param, sql)
        self.assertIn('[Квартал] IN (4)', inputs.author_sql(['5'], 'all_2026'))

    @patch('admin_motivation.services.subdivision_scope.sql_predicate', return_value='1=1')
    @patch('admin_motivation.services.motivation_exclusions.sql_allowed', return_value='1=1')
    def test_global_and_personal_performance_match_without_compaction(self, allowed, scope):
        self.stage()
        fields = ['Подразделение','Год','Квартал','ТипКоэф','ГруппаПлановПродаж','ГруппаТоваров','Марка','Канал','СуммаПланUSD','СуммаОтгрузокUSD']
        self.db.execute('CREATE TABLE plan ('+','.join('['+f+']' for f in fields)+')')
        for q in range(1, 5):
            for kind in ('Политики', 'Продажи'):
                row = ['Д-Москва', 2026, q, kind, 'Other', 'Other', 'Other', 'DPP', 100, 80]
                self.db.execute('INSERT INTO plan VALUES ('+','.join('?' for _ in row)+')', row)
        sql = self.sql(inputs.plan_sql('main')).replace('DataWH.motivation.PlanFact', 'plan')
        self.db.execute('CREATE TABLE plan_compact AS '+sql.replace('INTO plan_compact ', ''), ('Политики',))
        before = motivation_performance.grouped_sql('SELECT * FROM raw', 'Политики', 'main').replace('DataWH.motivation.PlanFact', 'plan')
        after = motivation_performance.grouped_sql('SELECT * FROM compact', 'Политики', 'main').replace('DataWH.motivation.PlanFact', 'plan_compact')
        self.assertEqual(self.rows(before), self.rows(after))

    @patch('admin_motivation.services.subdivision_aliases.rules', return_value=[])
    @patch('admin_motivation.services.subdivision_scope.sql_predicate', return_value='1=1')
    @patch('admin_motivation.services.motivation_exclusions.sql_allowed', return_value='1=1')
    @patch('admin_motivation.services.reports.connect_database')
    @patch('admin_motivation.services.reports.fetch', return_value=[])
    def test_loader_stages_once_and_keeps_independent_annual_query(self, fetch, connect, allowed, scope, aliases):
        from .services.reports import load_report
        connection, cursor = Mock(), Mock()
        connect.return_value = connection, cursor
        values = {'TypeCoeff_':'Политики', 'Subdivision':['Д-Москва'], 'DealAuthor':['Иван'],
                  'Years':['2026'], 'Currency':'RUB', 'gp_sales':['RAC'], 'GPGoods':[], 'Brend':[]}
        load_report('manager-detail', values, premium_period=['3'], summary_only=True,
                    calculation_variant='all_2026', include_performance=True,
                    period_authors_only=True, compact_summary=True)
        staged = [c.args for c in cursor.execute.call_args_list if ' INTO '+inputs.REPORT in c.args[0]]
        self.assertEqual(len(staged), 1)
        self.assertIn('Иван', staged[0][1:])
        self.assertIn('Д-Москва', staged[0][1:])
        self.assertIn('RAC', staged[0][1:])
        self.assertIn("LIKE N'3 кв%'", staged[0][0])
        queries = [c.args[1] for c in fetch.call_args_list]
        self.assertEqual(len(queries), 4)
        self.assertTrue(all(inputs.REPORT in q for q in queries[:3]))
        self.assertTrue(all(inputs.PLAN in q for q in queries[1:3]))
        self.assertNotIn(inputs.REPORT, queries[-1])
        self.assertIn("= N'год'", queries[-1])
        self.assertNotIn("LIKE N'3 кв%'", queries[-1])
        connection.close.assert_called_once()

        cursor.reset_mock()
        fetch.reset_mock()
        load_report('manager-detail', values, premium_period=['3'], summary_only=True,
                    calculation_variant='all_2026', include_performance=True, period_authors_only=True)
        cursor.execute.assert_not_called()
        self.assertTrue(all(inputs.REPORT not in c.args[1] for c in fetch.call_args_list))
