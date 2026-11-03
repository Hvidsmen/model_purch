from decimal import Decimal
import sqlite3
from unittest.mock import Mock, patch

from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from openpyxl import load_workbook

from .services.motivation_performance import department_sql, grouped_sql, plan_fact_table
from .services.reports_next_presenter import present
from .services.presentation_excel import workbook


class DepartmentPerformanceTests(SimpleTestCase):
    def test_whole_department_keeps_dpp_and_actual_q1_plan_without_employee_duplicates(self):
        with sqlite3.connect(':memory:') as db:
            db.create_function('LOWER', 1, lambda value: value.lower() if value else value)
            db.execute('CREATE TABLE input (Подразделение,Год,КварталМотивации,VariationCalculate,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка,СуммаПланUSD,СуммаОтгрузокUSD)')
            periods = ['1 Квартал', '2 Квартал(1-2 кв)', '3 Квартал', '4 Квартал(1-4 кв)', 'Год']
            db.executemany('INSERT INTO input VALUES (?,?,?,?,?,?,?,?,?,?)', [
                ('Д-Москва', 2026, period, 'Подразделение(Глобально)', author, 'PG', 'G', 'B', 999, 999)
                for author in ('Иван', 'Пётр') for period in periods])
            db.execute('CREATE TABLE plan_fact (Подразделение,Год,ТипКоэф,Квартал,Канал,СуммаПланUSD,СуммаОтгрузокUSD)')
            db.executemany('INSERT INTO plan_fact VALUES (?,?,?,?,?,?,?)', [
                ('Д-Москва', 2026, kind, q, channel, plan, fact)
                for q in (1, 2, 3, 4) for kind, channel, plan, fact in [
                    ('Продажи', 'Dealer', 100, 60), ('Продажи', 'ДПП', 50, 40),
                    ('Директора', 'Dealer', 9999, 9999), ('Политики', 'Dealer', 80, 50)]])
            db.executemany('INSERT INTO plan_fact VALUES (?,?,?,?,?,?,?)', [
                ('Д-СПб', 2026, 'Продажи', 3, 'Dealer', 9999, 9999),
                ('Д-Москва', 2025, 'Продажи', 3, 'Dealer', 9999, 9999)])
            for variant in ('main', 'all_2026'):
                def execute(sql):
                    return {row[2]: row[5:] for row in db.execute(sql.replace(plan_fact_table(variant), 'plan_fact').replace("N'", "'"))}
                actual = execute(department_sql('SELECT * FROM input', variant))
                self.assertEqual(actual, {'1 Квартал': (150, 100), '2 Квартал(1-2 кв)': (300, 200),
                    '3 Квартал': (150, 100), '4 Квартал(1-4 кв)': (600, 400), 'Год': (600, 400), 'Итого': (600, 400)})
                policies = execute(grouped_sql('SELECT * FROM input', 'Политики', variant))
                directors = execute(grouped_sql('SELECT * FROM input', 'Директора', variant))
                self.assertEqual(policies['1 Квартал'], (50, 50))
                self.assertEqual(directors['1 Квартал'], (100, 100))
                self.assertEqual(policies['3 Квартал'], (80, 50))
                self.assertEqual(directors['3 Квартал'], actual['3 Квартал'])
                # Two overlapping windows select quarters 1..3 once.
                sql = department_sql("SELECT * FROM input WHERE КварталМотивации IN ('2 Квартал(1-2 кв)','3 Квартал')", variant)
                self.assertEqual(execute(sql)['Итого'], (450, 300))

    def data(self):
        summary = {'columns': [{'label': '3 кв.'}], 'rows': [
            {'subdivision': 'Д-Москва', 'author': 'Иван', 'values': [Decimal(10)], 'possible_values': [Decimal(20)]}]}
        def metric(plan, fact):
            return {'Подразделение': 'Д-Москва', 'Год': 2026, 'КварталМотивации': '3 Квартал',
                    'VariationCalculate': 'Подразделение(Глобально)', 'Plan_': Decimal(plan), 'Fact_': Decimal(fact)}
        def report(kind, values, **kwargs):
            return {'period_summary': summary, 'motivation_performance': [metric(150 if values['TypeCoeff_'] == 'Директора' else 100, 80)],
                    'department_performance': [{**metric(170, 120), 'VariationCalculate': 'Общий план-факт'}]}
        return report

    @patch('admin_motivation.services.reports_next.restrict', return_value=(None, ['Политики', 'Продажи', 'Директора'], ['3']))
    def test_team_retains_three_types_and_one_independent_whole_department(self, restrict):
        from .services.reports_next import actual_reports
        with patch('admin_motivation.services.reports_next.load_report', side_effect=self.data()) as load:
            ledger = actual_reports(Mock(), Mock(parameters={'fact_year': 2026}), QueryDict('year=2026&period=3'))
        display = present('team', ledger, ledger, ['3'])
        self.assertTrue(all(call.kwargs['include_performance'] for call in load.call_args_list))
        self.assertEqual([row['kind'] for row in display['performance_matrix']['rows']], ['Политики', 'Продажи', 'Директора'])
        self.assertEqual(len(display['department_performance_matrix']['rows']), 1)
        overall = display['department_performance_matrix']['rows'][0]['cells'][0]
        self.assertEqual(overall['plan'], 170)
        self.assertEqual(display['total'], 30)
        html = render_to_string('admin_motivation/reports_next/_performance_sections.html', {'presentation': display})
        self.assertIn('План-факт подразделения', html)
        self.assertIn('План-факт по базам мотивации', html)
        self.assertEqual(html.count('<table'), 2)
        self.assertEqual(html.count('170,00'), 1)
        self.assertNotIn('Общая база подразделения', html)
        output = workbook('team', ledger, [])
        try:
            book = load_workbook(output, data_only=True)
            overall_rows = list(book['План-факт подразделения'].values)
            self.assertEqual(overall_rows[5][3:5], (170, 120))
            self.assertEqual(len(overall_rows), 6)
            self.assertEqual(len(list(book['План-факт'].values)), 8)
            book.close()
        finally:
            output.close()

    @patch('admin_motivation.reports_all_2026.reverse', return_value='/all-2026/')
    @patch('admin_motivation.reports_all_2026.selection', return_value=(None, ['Директора'], ['3']))
    def test_additional_report_has_whole_department_even_with_one_selected_type(self, selection, reverse):
        from .reports_all_2026 import data_for
        with patch('admin_motivation.reports_all_2026.reports_next.load_report', side_effect=self.data()):
            ledger = data_for(Mock(), Mock(), QueryDict('type=Директора&period=3'))
        display = present('registry', ledger, ledger, ['3'])
        self.assertEqual(len(display['department_performance_matrix']['rows']), 1)
        self.assertEqual(display['performance_matrix']['rows'][0]['kind'], 'Директора')
