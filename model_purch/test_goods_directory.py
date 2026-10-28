from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from base.testing import authorize_test_case
from .models import Freight, GoodsGroup, KindPurch, PGGoods, ScenarioModel, ScenarioPlanSales
from .services.goods_directory import REBUILD_SQL, SQL, NEW_GOODS_SQL, refresh_goods_directory


class GoodsDirectoryTests(TestCase):
    def setUp(self):
        self.user = authorize_test_case(self)
        self.scenario = ScenarioModel.objects.create(name='Plan', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.other = ScenarioModel.objects.create(name='Other', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        self.good = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Group', planning_sales='Sales',
            group_goods='Goods', brand='Brand', purch='Supplier', kind_purch=KindPurch.objects.create(name='Purchased'),
            volume=1, container_volume=70, duty_rate=7, exw_usd=10, stock_cnt_day=30, percent_stock_end=20)
        self.connection, self.cursor = Mock(), Mock()
        self.connector = Mock(return_value=(self.connection, self.cursor))
        self.cursor.fetchall.return_value = [(' GROUP ', 'New sales', 'New goods', 'New brand', 'PG1')]
        self.url = reverse('refresh_goods_directory', args=[self.scenario.pk])

    def refresh(self):
        return refresh_goods_directory(self.scenario, connector=self.connector)

    def test_only_three_fields_update_and_only_in_selected_scenario(self):
        other_good = PGGoods.objects.get(pk=self.good.pk)
        other_good.pk = None
        other_good.scenario_plan = self.other
        other_good.save()
        before = PGGoods.objects.values().get(pk=self.good.pk)
        self.assertEqual(self.refresh(), {'added': 0, 'updated': 1, 'unchanged': 0, 'missing': 0})
        after = PGGoods.objects.values().get(pk=self.good.pk)
        self.assertEqual({key for key in before if before[key] != after[key]}, {'planning_sales', 'group_goods', 'brand'})
        self.assertEqual((after['planning_sales'], after['group_goods'], after['brand'], after['purch']),
                         ('New sales', 'New goods', 'New brand', 'Supplier'))
        other_good.refresh_from_db()
        self.assertEqual(other_good.brand, 'Brand')
        self.assertTrue(GoodsGroup.objects.filter(name='New goods').exists())
        self.assertEqual(PGGoods.objects.count(), 2)
        self.connection.close.assert_called_once()
        self.connector.assert_called_once_with('vm-dwh', 'ModelPurch')
        self.assertEqual(self.refresh(), {'added': 0, 'updated': 0, 'unchanged': 1, 'missing': 0})

    def test_source_rebuild_precedes_read_and_commits_transaction(self):
        events = []
        self.cursor.execute.side_effect = lambda sql: events.append(sql)
        self.cursor.fetchall.side_effect = lambda: events.append('fetch') or [('Group', 'New', 'New', 'New', 'PG1')]
        self.connection.commit.side_effect = lambda: events.append('commit')
        self.refresh()
        self.assertEqual(events, [REBUILD_SQL, SQL, 'fetch', 'commit'])
        self.assertFalse(self.connection.autocommit)
        self.connection.rollback.assert_not_called()
        self.assertIn('DROP TABLE [ModelPurch].dbo.PlanningGroupOZP', REBUILD_SQL)
        self.assertIn('FROM DataWH.planning.Goods', REBUILD_SQL)
        self.assertIn('LEFT JOIN DataWH.dbo.ModelCodeERP', REBUILD_SQL)
        self.assertIn('WHERE RowId = 1', REBUILD_SQL)
        self.assertIn('sp_getapplock', REBUILD_SQL)

    def test_source_read_or_commit_failure_rolls_back_rebuild(self):
        for stage in ('fetchall', 'commit'):
            with self.subTest(stage=stage):
                self.connection.reset_mock()
                self.cursor.reset_mock()
                getattr(self.cursor if stage == 'fetchall' else self.connection, stage).side_effect = RuntimeError('SQL failed')
                with self.assertRaises(RuntimeError):
                    self.refresh()
                self.connection.rollback.assert_called_once()
                self.connection.close.assert_called_once()
                self.good.refresh_from_db()
                self.assertEqual(self.good.brand, 'Brand')
                self.cursor.fetchall.side_effect = None
                self.connection.commit.side_effect = None

    def test_unmatched_goods_preserved_new_source_goods_not_created(self):
        self.cursor.fetchall.return_value = [('Missing', 'Sales', 'Goods', 'Brand', 'PG2')]
        before = PGGoods.objects.values().get(pk=self.good.pk)
        self.assertEqual(self.refresh(), {'added': 0, 'updated': 0, 'unchanged': 0, 'missing': 1})
        self.assertEqual(PGGoods.objects.values().get(pk=self.good.pk), before)
        self.assertEqual(PGGoods.objects.count(), 1)

    def test_duplicate_identical_source_rows_accepted_and_blanks_clear_fields(self):
        self.cursor.fetchall.return_value = [('group', None, None, None, 'PG1'), ('GROUP', '', '', '', 'PG2')]
        self.assertEqual(self.refresh()['updated'], 1)
        self.good.refresh_from_db()
        self.assertEqual((self.good.planning_sales, self.good.group_goods, self.good.brand, self.good.purch), ('', '', None, 'Supplier'))

    def test_normalized_duplicates_prefer_pg_and_are_independent_of_source_order(self):
        candidates = [(' Group ', 'Model sales', 'Model goods', 'Model brand', 'A001'),
                      ('GROUP', 'PG sales', 'PG goods', 'PG brand', 'PG002'),
                      ('group', 'First sales', 'First goods', 'First brand', 'PG001')]
        for order in (candidates, list(reversed(candidates))):
            self.cursor.fetchall.return_value = order
            self.refresh()
            self.good.refresh_from_db()
            self.assertEqual((self.good.planning_sales, self.good.group_goods, self.good.brand),
                             ('First sales', 'First goods', 'First brand'))
        self.connection.rollback.assert_not_called()

    def test_equal_planning_keys_have_stable_whole_row_tiebreak(self):
        candidates = [('Group', 'B sales', 'A goods', 'B brand', 'PG1'),
                      ('Group', 'A sales', 'Z goods', 'A brand', 'PG1')]
        for order in (candidates, list(reversed(candidates))):
            self.cursor.fetchall.return_value = order
            self.refresh()
            self.good.refresh_from_db()
            self.assertEqual((self.good.planning_sales, self.good.group_goods, self.good.brand),
                             ('A sales', 'Z goods', 'A brand'))

    def test_oversized_values_cancel_entire_update(self):
        self.cursor.fetchall.return_value = [('Group', 'x' * 256, '', '', 'PG1')]
        with self.assertRaises(ValidationError):
            self.refresh()
        self.good.refresh_from_db()
        self.assertEqual(self.good.planning_sales, 'Sales')
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_connection_failure_closes_connection_and_preserves_goods(self):
        self.cursor.execute.side_effect = RuntimeError('rebuild failed')
        with self.assertRaises(RuntimeError):
            self.refresh()
        self.connection.close.assert_called_once()
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
        self.good.refresh_from_db()
        self.assertEqual(self.good.brand, 'Brand')

    def test_supplier_and_derived_prices_are_preserved(self):
        Freight.objects.create(scenario=self.scenario, price_per_container=700)
        self.good.save()
        before = PGGoods.objects.values().get(pk=self.good.pk)
        self.refresh()
        after = PGGoods.objects.values().get(pk=self.good.pk)
        self.assertEqual({key for key in before if before[key] != after[key]}, {'planning_sales', 'group_goods', 'brand'})
        self.assertEqual(after['purch'], 'Supplier')
        self.assertNotIn('[Purch]', SQL)

    def test_local_failure_rolls_back_metadata_and_new_groups(self):
        with patch('model_purch.services.goods_directory.PGGoods.objects.bulk_update', side_effect=ValidationError('Write failed')):
            with self.assertRaises(ValidationError):
                self.refresh()
        self.good.refresh_from_db()
        self.assertEqual(self.good.purch, 'Supplier')
        self.assertEqual(self.good.brand, 'Brand')
        self.assertFalse(GoodsGroup.objects.filter(name='New goods').exists())

    def test_empty_scenario_skips_connection(self):
        self.assertEqual(refresh_goods_directory(self.other, connector=self.connector), {'added': 0, 'updated': 0, 'unchanged': 0, 'missing': 0})
        self.connector.assert_not_called()

    def test_post_action_shows_counts_and_toolbar_button(self):
        with patch('model_purch.services.goods_directory.connect_database', self.connector):
            response = self.client.post(self.url)
        self.assertRedirects(response, reverse('pggoods_list') + f'?scenario={self.scenario.pk}', fetch_redirect_response=False)
        page = self.client.get(response.url)
        self.assertContains(page, 'Обновить справочник товаров')
        self.assertContains(page, 'Изменено: 1; без изменений: 0; не найдено в источнике: 0.')
        self.assertContains(page, self.url)

    def test_failure_message_and_post_only_archived_and_csrf_guards(self):
        with patch('model_purch.views_goods_directory.refresh_goods_directory') as refresh:
            self.assertEqual(self.client.get(self.url).status_code, 405)
            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(self.user)
            self.assertEqual(csrf_client.post(self.url).status_code, 403)
            self.other.archived_at = '2026-10-20T12:00:00Z'
            self.other.save(update_fields=['archived_at'])
            self.assertEqual(self.client.post(reverse('refresh_goods_directory', args=[self.other.pk])).status_code, 404)
            refresh.assert_not_called()
            refresh.side_effect = RuntimeError('SECRET connection details')
            with self.assertLogs('model_purch.views_goods_directory', level='ERROR'):
                response = self.client.post(self.url, follow=True)
            self.assertContains(response, 'Изменения не сохранены')
            self.assertNotContains(response, 'SECRET')

    def attach_plan(self, scenario=None):
        return ScenarioPlanSales.objects.create(scenario_model=scenario or self.scenario, name="Plan 'quoted'", flag_order_in_purch=True)

    def test_new_planned_goods_added_with_group_defaults_without_rewriting_existing_inputs(self):
        plan = self.attach_plan()
        ScenarioModel.objects.filter(pk=self.scenario.pk).update(overwrite_existing=True)
        GoodsGroup.objects.create(name='New goods', duty_rate=.1, container_volume=54)
        self.cursor.fetchall.side_effect = [
            [('Group', 'New sales', 'New goods', 'New brand', 'PG1')],
            [(' New group ', 'Sales 2', 'New goods', 'Brand 2', 'PG2', 'Supplier 2', 1.25),
             ('NEW GROUP', 'Sales 2', 'New goods', 'Brand 2', 'PG2', 'Supplier 2', 1.25)]]
        before = PGGoods.objects.values().get(pk=self.good.pk)
        self.scenario.refresh_from_db()
        counts = self.refresh()
        self.assertEqual(counts, {'added': 1, 'updated': 1, 'unchanged': 0, 'missing': 0})
        new = PGGoods.objects.get(scenario_plan=self.scenario, planning_group__iexact='New group')
        self.assertEqual((new.exw_usd, new.volume, new.purch, new.duty_rate, new.container_volume), (0, 1.25, 'Supplier 2', .1, 54))
        self.assertEqual(new.kind_purch.name, 'Закупается')
        after = PGGoods.objects.values().get(pk=self.good.pk)
        self.assertEqual({key for key in before if before[key] != after[key]}, {'planning_sales','group_goods','brand'})
        query = self.cursor.execute.call_args_list[-1].args
        self.assertEqual(query[1:], (self.scenario.date_start_plan, self.scenario.date_end_plan, plan.name))
        self.assertNotIn(plan.name, query[0])
        self.assertIn('p.Сценарий IN (?)', query[0])
        self.assertFalse(PGGoods.objects.filter(scenario_plan=self.other).exists())
        self.cursor.fetchall.side_effect = [
            [('Group', 'New sales', 'New goods', 'New brand', 'PG1'), ('New group', 'Sales 2', 'New goods', 'Brand 2', 'PG2')],
            [('New group', 'Sales 2', 'New goods', 'Brand 2', 'PG2', 'Supplier 2', 1.25)]]
        self.assertEqual(self.refresh(), {'added': 0, 'updated': 0, 'unchanged': 2, 'missing': 0})
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.scenario).count(), 2)

    def test_empty_scenario_can_be_populated_from_attached_plan(self):
        self.attach_plan(self.other)
        self.cursor.fetchall.side_effect = [[], [('New', None, None, None, None, None, None)]]
        counts = refresh_goods_directory(self.other, connector=self.connector)
        self.assertEqual(counts['added'], 1)
        new = PGGoods.objects.get(scenario_plan=self.other)
        self.assertEqual((new.planning_group, new.exw_usd, new.volume, new.purch), ('New', 0, 0, None))
        self.assertEqual((new.planning_sales, new.group_goods, new.brand), ('', '', None))
        self.assertIn('LEFT JOIN ModelPurch.dbo.PlanningGroupOZP', NEW_GOODS_SQL)

    def test_invalid_new_goods_roll_back_all_local_changes_and_source_transaction(self):
        self.attach_plan()
        for volume in (-1, float('nan'), float('inf'), 'bad'):
            with self.subTest(volume=volume):
                self.cursor.fetchall.side_effect = [[('Group', 'Changed', 'Goods', 'Brand', 'PG1')],
                    [('New', 'Sales', 'Goods', 'Brand', 'PG2', 'Supplier', volume)]]
                with self.assertRaises(ValidationError): self.refresh()
                self.good.refresh_from_db()
                self.assertEqual(self.good.planning_sales, 'Sales')
                self.assertEqual(PGGoods.objects.filter(scenario_plan=self.scenario).count(), 1)
        self.connection.commit.assert_not_called()

    def test_failure_during_creation_rolls_back_metadata_and_group_creation(self):
        self.attach_plan()
        self.cursor.fetchall.side_effect = [[('Group', 'Changed', 'New goods', 'Brand', 'PG1')],
                    [('New', 'Sales', 'New goods', 'Brand', 'PG2', 'Supplier', 1)]]
        with patch('model_purch.services.goods_directory.PGGoods.objects.get_or_create', side_effect=ValidationError('Creation failed')):
            with self.assertRaises(ValidationError): self.refresh()
        self.good.refresh_from_db()
        self.assertEqual(self.good.planning_sales, 'Sales')
        self.assertFalse(GoodsGroup.objects.filter(name='New goods').exists())
        self.assertEqual(PGGoods.objects.count(), 1)

    def test_changed_plan_configuration_during_fetch_cancels_local_update(self):
        plan = self.attach_plan()
        def fetch():
            if self.cursor.fetchall.call_count == 1:
                return [('Group', 'Changed', 'Goods', 'Brand', 'PG1')]
            ScenarioPlanSales.objects.filter(pk=plan.pk).update(name='Changed plan')
            return [('New', 'Sales', 'Goods', 'Brand', 'PG2', 'Supplier', 1)]
        self.cursor.fetchall.side_effect = fetch
        with self.assertRaisesMessage(ValidationError, 'изменились во время загрузки'):
            self.refresh()
        self.good.refresh_from_db()
        self.assertEqual(self.good.planning_sales, 'Sales')
        self.assertEqual(PGGoods.objects.count(), 1)

    def test_invalid_creation_defaults_for_existing_goods_do_not_overwrite_or_block_refresh(self):
        self.attach_plan()
        self.cursor.fetchall.side_effect = [[('Group', 'Sales', 'Goods', 'Brand', 'PG1')],
            [('Group', 'Sales', 'Goods', 'Brand', 'PG1', 'Other supplier', -10)]]
        before = PGGoods.objects.values().get(pk=self.good.pk)
        self.assertEqual(self.refresh(), {'added': 0, 'updated': 0, 'unchanged': 1, 'missing': 0})
        self.assertEqual(PGGoods.objects.values().get(pk=self.good.pk), before)
