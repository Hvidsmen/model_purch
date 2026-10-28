from decimal import Decimal
from unittest.mock import Mock, patch
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from base.models import UserProjectAccess
from base.testing import authorize_test_case
from .models import PGGoods, KindPurch, ScenarioModel, ScenarioPlanSales
from .services.goods_presence import check_goods_presence, SOURCES


class GoodsPresenceTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.scenario = ScenarioModel.objects.create(name='Plan', date_start_plan='2027-01-01', date_end_plan='2027-11-30')
        ScenarioPlanSales.objects.create(scenario_model=self.scenario, name="Plan '2027'", flag_order_in_purch=True)
        self.good = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Daikin Spl (ind) Sensira (FTXF)', planning_sales='', group_goods='99. К разбору',
            kind_purch=KindPurch.objects.create(name='Не закупается'), volume=.12, exw_usd=19.28, stock_cnt_day=0, percent_stock_end=0)
        self.good = PGGoods.objects.select_related('scenario_plan').get(pk=self.good.pk)
        self.connection, self.cursor = Mock(), Mock()
        self.connector = Mock(return_value=(self.connection,self.cursor))
        self.rows = [[key,0,None,None,None] for key in SOURCES]
        self.cursor.fetchall.return_value = self.rows
        self.url = reverse('goods_presence',args=[self.good.pk])

    def check(self):
        return check_goods_presence(self.good,self.connector)

    def test_incoming_only_explains_presence_without_writing_anything(self):
        self.rows[-1] = ['income',2,20,10,None]
        before = PGGoods.objects.values().get(pk=self.good.pk)
        result = self.check()
        self.assertTrue(result['supported'])
        self.assertEqual([row['key'] for row in result['rows'] if row['qualifies']], ['income'])
        self.assertEqual(PGGoods.objects.values().get(pk=self.good.pk),before)
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once()
        args = self.cursor.execute.call_args.args
        self.assertEqual(args[1:], ('2027-01-01','2027-11-30',self.good.planning_group,"Plan '2027'","Plan '2027'"))
        self.assertEqual(args[0].count('?'),len(args)-1)
        for keyword in ('DROP ', 'INSERT ', 'UPDATE ', 'DELETE ', ' EXEC '):
            self.assertNotIn(keyword,args[0].upper())

    def test_stock_full_cost_divided_by_accounting_quantity_and_zero_denominator(self):
        self.rows[2] = ['stock_cost',2,20,10,6000]
        row = self.check()['rows'][2]
        self.assertEqual(row['ddp'],Decimal(600))
        self.rows[2] = ['stock_cost',2,20,0,6000]
        row = self.check()['rows'][2]
        self.assertFalse(row['qualifies'])
        self.assertTrue(row['found'])
        self.assertIsNone(row['ddp'])
        self.rows[3] = ['stock_initial',1,10,0,None]
        self.assertTrue(self.check()['rows'][3]['qualifies'])  # Legacy inclusion used positive raw quantity.

    def test_missing_or_failed_sources_never_report_successful_absence(self):
        self.assertFalse(self.check()['supported'])
        self.cursor.fetchall.return_value = self.rows[:-1]
        with self.assertRaises(ValidationError): self.check()
        self.cursor.execute.side_effect = RuntimeError('Source unavailable')
        with self.assertRaises(RuntimeError): self.check()
        self.assertGreaterEqual(self.connection.close.call_count,3)

    def test_page_is_on_demand_and_has_no_deletion_action(self):
        with patch('model_purch.views_goods_presence.check_goods_presence') as check:
            self.assertEqual(self.client.get(self.url).status_code,200)
            check.assert_not_called()
        self.rows[-1] = ['income',1,10,10,None]
        with patch('model_purch.views_goods_presence.check_goods_presence',return_value=self.check()):
            response = self.client.get(self.url,{'check':'1'})
        self.assertContains(response,'Ожидаемые поступления')
        self.assertContains(response,'Есть данные в источниках')
        self.assertContains(response,'не история появления строки')
        self.assertNotContains(response,reverse('delete_pggoods',args=[self.good.pk]))
        self.assertEqual(self.client.post(self.url).status_code,405)
        with patch('model_purch.views_goods_presence.check_goods_presence',side_effect=RuntimeError('SECRET')):
            with self.assertLogs('model_purch.views_goods_presence',level='ERROR'):
                response = self.client.get(self.url,{'check':'1'})
        self.assertContains(response,'Отсутствие товара не подтверждено')
        self.assertNotContains(response,'SECRET')

    def test_analyst_can_read_but_archived_scenario_is_not_exposed(self):
        user = User.objects.create_user('presence-analyst')
        UserProjectAccess.objects.create(user=user,project='purchases',role='Аналитик')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code,200)
        with patch('model_purch.views_goods_presence.check_goods_presence') as check:
            ScenarioModel.objects.filter(pk=self.scenario.pk).update(archived_at='2026-10-29T12:00:00Z')
            self.assertEqual(self.client.get(self.url,{'check':'1'}).status_code,404)
            check.assert_not_called()

    def test_page_header_and_scenario_switcher_are_bound_to_the_goods_scenario(self):
        other = ScenarioModel.objects.create(name='Other',date_start_plan='2028-01-01',date_end_plan='2028-12-31')
        session = self.client.session
        session['model_purch_scenario_id'] = other.pk
        session.save()
        response = self.client.get(self.url)
        self.assertEqual(response.context['workspace_scenario'].pk,self.scenario.pk)
        self.assertEqual(response.context['workspace_selection_url'],reverse('pggoods_list'))
        self.assertEqual(self.client.get(self.url,{'scenario':other.pk}).status_code,404)
