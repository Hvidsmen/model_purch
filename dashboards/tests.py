from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from base.access import ROLES, ADMIN, ANALYST, ensure_roles
from .services.dealers import analyze, segment, periods
from .services.source import fetch_sales


def sale(code, year, qty, *, month=1, branch='Москва', brand='DAICHI', equipment='Split', name=None, amount=None):
    return {'dealer_code': code, 'dealer_name': name or 'Дилер '+code, 'month': date(year, month, 1),
            'quantity': Decimal(str(qty)), 'amount': Decimal(str(amount if amount is not None else qty*100)),
            'branch': branch, 'brand': brand, 'equipment': equipment, 'region': 'Регион', 'city': 'Город',
            'product_range': 'Range', 'planning_group': 'RAC'}


def fixture():
    return [sale('grow', 2021, 5), sale('grow', 2022, 30), sale('grow', 2023, 80), sale('grow', 2024, 300), sale('grow', 2025, 350),
            sale('lost', 2021, 50), sale('lost', 2022, 30),
            sale('return', 2021, 20), sale('return', 2024, 25), sale('return', 2025, 20),
            sale('new', 2024, 7), sale('new', 2025, 8), sale('middle', 2022, 100),
            sale('returns-only', 2025, -2), sale('grow', 2025, 10, month=11), sale('grow', 2026, 20, month=2)]


OPTIONS = {'method': 'threshold', 'scope': 'company', 'measure': 'quantity', 'period_mode': 'calendar', 'start': 2021, 'end': 2025}


class DealerAnalyticsTests(SimpleTestCase):
    def report(self, rows=None, **options):
        return analyze(rows if rows is not None else fixture(), {**OPTIONS, **options}, date(2026, 10, 24))

    def test_custom_size_limits_apply_to_all_periods_and_preserve_bridge_balance(self):
        default = self.report()
        custom = self.report(s_max=25, m_max=100, l_max=500)
        self.assertEqual(custom['thresholds'], (25, 100, 500))
        self.assertNotEqual(default['overview'][-1]['groups'], custom['overview'][-1]['groups'])
        self.assertTrue(custom['bridge']['balanced'])
        self.assertIn('0–25', custom['rules']['threshold'])

    def test_threshold_boundaries_and_net_returns(self):
        values = {str(q): {'quantity': Decimal(q), 'amount': Decimal(10)} for q in [-1, 0, 1, 10, 11, 50, 51, 250, 251]}
        self.assertEqual(segment(values, 'threshold', 'quantity'), {'1':'S','10':'S','11':'M','50':'M','51':'L','250':'L','251':'XL'})

    def test_abc_crossing_dealer_and_ties_are_deterministic(self):
        values = {code: {'quantity': Decimal(value), 'amount': Decimal(value)} for code, value in [('x',85),('z',5),('a',5),('b',5)]}
        expected={'x':'A','a':'B','b':'B','z':'C'}
        self.assertEqual(segment(values, 'abc', 'quantity'), expected)
        self.assertEqual(segment(dict(reversed(list(values.items()))), 'abc', 'quantity'), expected)
        self.assertEqual(segment({'one': values['x']}, 'abc', 'quantity'), {'one':'A'})

    def test_ltm_has_twelve_complete_months_and_calendar_has_no_partial_current_year(self):
        timeline=periods(date(2026,10,24))
        self.assertEqual(timeline[-1]['start'],date(2025,10,1))
        self.assertEqual(timeline[-1]['end'],date(2026,10,1))
        data=self.report(period_mode='ltm',end=2026)
        self.assertEqual(data['overview'][-1]['quantity'],30)
        self.assertEqual(periods(date(2026,10,24),'calendar')[-1]['year'],2025)

    def test_bridge_balances_for_signed_returns_and_both_methods_and_measures(self):
        for method in ('abc','threshold'):
            for measure in ('quantity','amount'):
                data=self.report(method=method,measure=measure)
                self.assertTrue(data['bridge']['balanced'])
                self.assertEqual(data['bridge']['end']-data['bridge']['start'],sum(item['value'] for item in data['bridge']['items']))
                self.assertIn('Возвраты без активных продаж',[item['label'] for item in data['bridge']['items']])
        self.assertTrue(all(flow['source']!='Новые' or flow['target']!='Ушли' for flow in data['flows']))

    def test_new_returned_lost_and_trajectory_only_present_in_middle_period(self):
        report=self.report()
        self.assertTrue(any(flow['source']=='Новые' and flow['target']=='S' for flow in report['flows']))
        dealer=self.report(dealer='return')['dealer']
        self.assertEqual(next(p for p in dealer['periods'] if p['year']==2024)['groups'][0]['status'],'Вернулся')
        self.assertEqual(next(p for p in dealer['periods'] if p['year']==2022)['groups'][0]['status'],'Ушёл')
        self.assertTrue(any(t['path']==['Нет продаж','L','Нет продаж','Нет продаж','Нет продаж'] for t in report['trajectories']))
        self.assertEqual(report['arrivals'][0]['size'],3)

    def test_consolidation_uses_code_and_company_segmentation_precedes_branch_filter(self):
        rows=[sale('same',2025,5,branch='Москва',name='Старое имя'),sale('same',2025,300,branch='Омск',name='Новое имя'),sale('another',2025,8,branch='Москва')]
        data=self.report(rows,branch=['Москва'])
        self.assertEqual(data['overview'][-1]['dealers'],2)
        self.assertEqual(next(g for g in data['overview'][-1]['groups'] if g['group']=='XL')['count'],1)
        scoped=self.report(rows,branch=['Москва'],scope='branch')
        self.assertEqual(next(g for g in scoped['overview'][-1]['groups'] if g['group']=='S')['count'],2)
        self.assertEqual(self.report(rows)['overview'][-1]['dealers'],2)
        self.assertEqual(self.report(rows,scope='branch')['overview'][-1]['dealers'],3)

    def test_same_code_has_one_search_option_even_when_branch_names_differ(self):
        rows=[sale('one',2024,5,branch='Москва',name='До переименования'),sale('one',2025,10,branch='Омск',name='После переименования')]
        data=self.report(rows,dealer='one')
        self.assertEqual(data['dealer_matches'],1)
        self.assertEqual(data['dealer_options'][0]['name'],'После переименования')
        self.assertIsNotNone(data['dealer'])
        self.assertIsNone(self.report(rows,dealer='unknown')['dealer'])

    def test_movement_does_not_call_a_dealer_lost_before_first_sale(self):
        dealer=self.report(dealer='new')['dealer']
        self.assertEqual(dealer['periods'][0]['groups'][0]['status'],'Нет продаж')
        self.assertEqual(next(p for p in dealer['periods'] if p['year']==2024)['groups'][0]['status'],'Новый')

    def test_trace_filter_preserves_company_bridge_and_overview(self):
        full,trace=self.report(),self.report(cohort='M')
        self.assertEqual(full['bridge'],trace['bridge'])
        self.assertEqual(full['overview'],trace['overview'])
        self.assertTrue(all(flow['source']=='M' for flow in trace['flows']))

    def test_company_wide_return_cannot_produce_an_active_dealer_without_a_group(self):
        rows=[sale('zero',2025,10,branch='Москва'),sale('zero',2025,-10,branch='Омск')]
        company=self.report(rows,branch=['Москва'])
        self.assertEqual(company['overview'][-1]['dealers'],0)
        self.assertEqual(sum(group['count'] for group in company['overview'][-1]['groups']),0)
        self.assertTrue(company['bridge']['balanced'])
        branch=self.report(rows,branch=['Москва'],scope='branch')
        self.assertEqual(branch['overview'][-1]['dealers'],1)

    def test_thresholds_use_quantity_even_when_amount_is_selected(self):
        rows=[sale('one',2025,5,amount=1000000)]
        self.assertEqual(self.report(rows,measure='amount')['overview'][-1]['groups'][0]['count'],1)

    def test_empty_selection_and_filtered_data_do_not_break_totals_or_filter_menus(self):
        data=self.report(brand=['Not present'])
        self.assertTrue(data['empty']);self.assertTrue(data['bridge']['balanced'])
        self.assertIn('DAICHI',data['filters']['brand'])
        self.assertEqual(data['overview'][-1]['dealers'],0)

    def test_cohort_filter_only_traces_selected_initial_group(self):
        data=self.report(cohort='M')
        self.assertTrue(all(flow['source']=='M' for flow in data['flows']))
        self.assertTrue(data['bridge']['balanced'])


class DashboardAccessTests(TestCase):
    def setUp(self):
        ensure_roles()
        self.admin=User.objects.create_superuser('dashboard-admin')
        cache.clear()

    def test_admin_and_analyst_can_read_pages_data_and_exports(self):
        routes=('dashboards_index','dealer_dashboard','dealer_dashboard_data','dealer_dashboard_export')
        for role in ROLES:
            user=User.objects.create_user('role-'+str(ROLES.index(role)))
            user.groups.add(Group.objects.get(name=role))
            self.client.force_login(user)
            for route in routes:
                with patch('dashboards.views.load_sales',return_value={'rows':fixture(),'loaded_at':'2026-10-24T10:00:00Z','end_exclusive':'2026-10-01'}):
                    self.assertEqual(self.client.get(reverse(route)).status_code,200 if role in (ADMIN, ANALYST) else 403,(role,route))
        self.client.logout()
        self.assertEqual(self.client.get(reverse('dealer_dashboard_data')).status_code,302)
        self.assertEqual(self.client.get(reverse('dealer_dashboard_data'),HTTP_ACCEPT='application/json').status_code,401)

    def test_invalid_thresholds_are_rejected_before_reading_data(self):
        self.client.force_login(self.admin)
        with patch('dashboards.views.load_sales') as source:
            for values in ({'s_max': '100', 'm_max': '50'}, {'s_max': '0'}, {'m_max': '10'}, {'l_max': 'nan'}, {'s_max': '1.5'}):
                self.assertEqual(self.client.get(reverse('dealer_dashboard_data'), values).status_code, 400)
            source.assert_not_called()

    def test_navigation_has_third_project_for_admin_only(self):
        self.client.force_login(self.admin)
        page=self.client.get(reverse('index_base'))
        self.assertContains(page,'Дашборды')
        self.assertContains(page,reverse('dashboards_index'))

    def test_api_and_export_share_identical_filters_and_errors_do_not_expose_connection_details(self):
        self.client.force_login(self.admin)
        with patch('dashboards.views.load_sales',return_value={'rows':fixture(),'loaded_at':'2026-10-24T10:00:00Z','end_exclusive':'2026-10-01'}):
            response=self.client.get(reverse('dealer_dashboard_data'),{'method':'threshold','period_mode':'calendar'})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['bridge']['balanced'])
            export=self.client.get(reverse('dealer_dashboard_export'),{'method':'threshold','period_mode':'calendar'})
            self.assertEqual(export.status_code,200)
            self.assertIn('Выручка без IT',export.content.decode())
            self.assertEqual(self.client.get(reverse('dealer_dashboard_data'),{'measure':'invalid'}).status_code,400)
        with patch('dashboards.views.load_sales',side_effect=RuntimeError('password=secret')):
            page=self.client.get(reverse('dealer_dashboard_data'))
            self.assertEqual(page.status_code,503)
            self.assertNotIn('secret',page.content.decode())
        self.assertEqual(self.client.post(reverse('dealer_dashboard_data')).status_code,405)

    def test_source_is_parameterized_monthly_and_deducts_it_without_quantity_multiplier(self):
        connection,cursor=Mock(),Mock()
        columns=list(fixture()[0])
        cursor.description=[(name,) for name in columns]
        cursor.fetchmany.side_effect=[[tuple(fixture()[0][name] for name in columns)],[]]
        with patch('dashboards.services.source.connect_database',return_value=(connection,cursor)) as connect:
            result=fetch_sales(date(2026,10,24))
            sql,*parameters=cursor.execute.call_args.args
            self.assertIn('sub.Chanel =',sql)
            self.assertIn('s.Price - COALESCE(s.AmountUSDIt_ITW, 0)',sql)
            self.assertIn("'Split Cloud'",sql)
            self.assertIn('DATEFROMPARTS',sql)
            self.assertEqual(parameters,[date(2021,1,1),date(2026,10,1)])
            self.assertEqual(result['rows'][0]['dealer_code'],'grow')
            fetch_sales(date(2026,10,25));connect.assert_called_once()
        connection.close.assert_called_once()

    def test_failed_source_closes_connection_and_is_not_cached(self):
        connection,cursor=Mock(),Mock()
        cursor.execute.side_effect=RuntimeError('failed')
        with patch('dashboards.services.source.connect_database',return_value=(connection,cursor)) as connect:
            for attempt in range(2):
                with self.assertRaises(RuntimeError):fetch_sales(date(2026,10,24))
            self.assertEqual(connect.call_count,2)
        self.assertEqual(connection.close.call_count,2)
