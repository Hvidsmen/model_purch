from datetime import date
from unittest.mock import Mock, patch

from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import TestCase
from openpyxl import load_workbook

from base.report_access import scoped_values, scoped_query
from .services.manager_detail import manager_detail_report
from .services.report_excel import report_excel
from .services.report_periods import PILOT_QUARTER, PILOT_SQL, allowed_period
from .services.reports import choices, load_report, manager_report
from . import test_reports as fixtures


class PilotPeriodTests(TestCase):
    scope = {'subdivision': 'Own', 'author': 'Employee', 'type': 'Политики', 'pilot_periods': True}
    periods = ['1 Квартал', '2 Квартал(1-2 кв)', '3 Квартал', PILOT_QUARTER, 'Год']

    def values(self):
        return {'TypeCoeff_': 'Политики', 'Subdivision': 'Own', 'DealAuthor': 'Employee',
                'gp_sales': [], 'qm': '1 Квартал', 'Currency': 'USD', 'Years': ['2024', '2025'],
                'GPGoods': [], 'Brend': []}

    def detail_rows(self):
        return [dict(fixtures.NativeMotivationReportTests.division_rows()[0], КварталМотивации=period,
                     АвторСделки='Employee', Год=2024) for period in self.periods]

    def test_stale_or_tampered_quarters_are_normalized_without_restricting_years(self):
        for period in [None, '', '1 Квартал', '2 Квартал(1-2 кв)', '3 Квартал', 'anything']:
            values = scoped_values('manager', {**self.values(), 'qm': period}, self.scope)
            self.assertEqual(values['qm'], PILOT_QUARTER)
            self.assertEqual(values['Years'], ['2024', '2025'])
        for period in [PILOT_QUARTER, '4 кв.', 'Год']:
            self.assertEqual(scoped_values('manager', {**self.values(), 'qm': period}, self.scope)['qm'], period)
        query = scoped_query(QueryDict('quarter=1+Квартал&quarter=Год'), self.scope, 'manager')
        self.assertEqual(query.getlist('quarter'), [PILOT_QUARTER])
        self.assertEqual(scoped_values('manager', self.values(), {**self.scope, 'pilot_periods': False})['qm'], '1 Квартал')

    def test_predicate_matches_actual_period_labels(self):
        import sqlite3
        with sqlite3.connect(':memory:') as db:
            db.create_function('LOWER', 1, lambda value: value.lower() if value is not None else None)
            db.execute('CREATE TABLE periods ([КварталМотивации])')
            labels = self.periods + [' 4 кв. ', '40 Квартал', 'Без периода']
            db.executemany('INSERT INTO periods VALUES (?)', [(label,) for label in labels])
            actual = [row[0] for row in db.execute('SELECT * FROM periods WHERE ' + PILOT_SQL.replace("N'", "'"))]
        self.assertEqual(actual, [label for label in labels if allowed_period(label)])

    def test_all_lookup_queries_exclude_early_periods_but_deal_lookups_do_not(self):
        with patch('admin_motivation.services.personal_reports.connect_database', return_value=(Mock(), Mock())), patch(
                'admin_motivation.services.personal_reports.fetch', return_value=[{'value': p} for p in self.periods]) as read:
            options = choices('manager', self.values(), access_scope=self.scope)
            self.assertEqual([o['value'] for o in next(f for f in options if f['name'] == 'quarter')['options']], [PILOT_QUARTER, 'Год'])
            self.assertTrue(all(PILOT_SQL in call.args[1] for call in read.call_args_list))
            read.reset_mock()
            choices('deals', {'TypeCoeff': 'Политики', 'Division': 'Own', 'ManagerName': 'Employee',
                             'StartDate': date(2024, 1, 1), 'EndDate': date(2024, 12, 31)}, access_scope=self.scope)
            self.assertTrue(read.called)
            self.assertTrue(all(PILOT_SQL not in call.args[1] for call in read.call_args_list))

    def test_detail_totals_and_excel_contain_only_q4_and_year(self):
        rows = self.detail_rows()
        report = manager_detail_report(rows, performance_rows=rows, pilot=True)
        self.assertEqual(report['periods'], [PILOT_QUARTER, 'Год'])
        self.assertEqual([c['label'] for c in report['period_summary']['columns']], ['4 кв.', 'Год'])
        self.assertEqual(report['period_summary']['totals']['total'], 160)
        book = load_workbook(report_excel(report, self.values()), read_only=True)
        self.addCleanup(book.close)
        cells = [str(cell) for sheet in book for row in sheet.values for cell in row if cell is not None]
        for forbidden in self.periods[:3] + ['1 кв.', '2 кв.', '3 кв.']:
            self.assertNotIn(forbidden, cells)
        self.assertTrue(any(cell.startswith(PILOT_QUARTER + '\n') for cell in cells))
        self.assertEqual(len(manager_detail_report(rows)['period_summary']['columns']), 5)

    def test_dashboard_breakdown_and_html_hide_early_quarters(self):
        rows = [dict(fixtures.NativeMotivationReportTests.manager_rows()[0], КварталМотивации=p) for p in self.periods]
        report = manager_report(rows, pilot=True)
        self.assertEqual(report['year_periods'], '45')
        self.assertEqual(report['totals'], manager_report(rows[3:])['totals'])
        hidden = [cell for cell in report['total_blocks'] if cell.get('hidden') == 'year-possible']
        self.assertEqual(len(hidden), 2)
        html = render_to_string('admin_motivation/reports/manager.html', {'data': report, 'query': {'quarter': PILOT_QUARTER}})
        self.assertIn('data-year-period-count="2"', html)
        for period in ['1 кв.', '2 кв.', '3 кв.']:
            self.assertNotIn(period, html)
        self.assertEqual(manager_report(rows)['year_periods'], '12345')

    def test_data_and_performance_queries_filter_before_aggregation(self):
        for kind in ['manager', 'manager-detail']:
            rows = self.detail_rows() if kind == 'manager-detail' else []
            results = [rows, rows] if kind == 'manager-detail' else [[], [], []]
            with patch('admin_motivation.services.reports.connect_database', return_value=(Mock(), Mock())), patch(
                    'admin_motivation.services.reports.fetch', side_effect=results) as read:
                result = load_report(kind, self.values(), access_scope=self.scope)
            self.assertTrue(all(PILOT_SQL in call.args[1] for call in read.call_args_list))
            self.assertTrue(all('1 Квартал' not in call.args[2] for call in read.call_args_list))
            if kind == 'manager-detail':
                self.assertEqual(result['periods'], [PILOT_QUARTER, 'Год'])

    def test_deal_rows_and_date_range_remain_unrestricted(self):
        values = {'TypeCoeff': 'Политики', 'Division': 'Own', 'ManagerName': 'Employee', 'GPSales': [],
                  'StartDate': date(2024, 1, 1), 'EndDate': date(2024, 12, 31),
                  'Channel': [], 'GPGoods': [], 'Brend': [], 'Partner': []}
        with patch('admin_motivation.services.reports.connect_database', return_value=(Mock(), Mock())), patch(
                'admin_motivation.services.reports.fetch', side_effect=[[], [{'ДатаЗакрытияСделки': date(2024, 2, 1)}]]) as read:
            result = load_report('deals', values, access_scope=self.scope)
        self.assertEqual(result['details'][0]['ДатаЗакрытияСделки'], date(2024, 2, 1))
        for call in read.call_args_list:
            self.assertNotIn(PILOT_SQL, call.args[1])
            self.assertIn(date(2024, 1, 1), call.args[2])
            self.assertIn(date(2024, 12, 31), call.args[2])
