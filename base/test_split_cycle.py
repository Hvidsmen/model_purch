from copy import deepcopy
from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import SalesPlanningChannel, SalesPlanningGoods, SalesPlanningDirectoryLoad, SalesPlanningGroup, SplitScenario
from .sales_planning_group_service import rebuild_groups, save_pair
from .split_engine import number, allocate, evaluate, planning_months, quantity
from .split_workflow import create_scenario, persist, approve, correction, changed_state, forecast_preview
from .split_forms import ScenarioForm


class DecimalDistributionTests(SimpleTestCase):
    def test_exact_totals_nonnegative_and_zero_weights_receive_no_rounding_residue(self):
        for total in ['1', '.000001', '999999999999']:
            values = allocate(number(total), [number('33.333333'), number('66.666667'), number(0)])
            self.assertEqual(sum(values), number(total))
            self.assertEqual(values[-1], 0)
            self.assertTrue(all(value >= 0 for value in values))
        for value in ['NaN', 'Infinity', '-1', '.0000001', '1000000000000', '=1+1']:
            with self.assertRaises(ValidationError):
                number(value)


class SplitCycleTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser('split-cycle-admin')
        self.client.force_login(self.actor)
        self.channels = [SalesPlanningChannel.objects.create(name=name) for name in ('Дилеры', 'Розница')]
        SalesPlanningDirectoryLoad.objects.create(directory='goods', loaded_at=timezone.now(), row_count=2)
        self.models = [SalesPlanningGoods.objects.create(source_key='m' + str(index), name=name, planning_group='G',
            product_group='Бытовые кондиционеры', brand='Brand', power_class='07', block_type=block)
            for index, (name, block) in enumerate([('IN', 'Внутренний'), ('OUT', 'Наружный')])]
        rebuild_groups()
        self.group = SalesPlanningGroup.objects.get()
        save_pair(self.group, '07', self.models[0].pk, self.models[1].pk)
        self.scenario = create_scenario(self.actor, {'title': 'План 2027', 'year': 2027,
            'start': date(2027, 1, 1), 'end': date(2027, 12, 31), 'stock_date': date(2026, 12, 31),
            'entry_mode': 'period', 'groups': [self.group], 'channels': self.channels})

    def complete(self, scenario=None):
        item = scenario or self.scenario
        state = deepcopy(item.state)
        for group in state['groups'].values():
            group['ddp'] = '100'
            for index, line in enumerate(group['channels'].values()):
                line['input'], line['markup'], line['fact'] = str((index + 1) * 10), '1.5', '0'
                line['months'] = dict(zip(planning_months(item), map(str, allocate(number(100), [number(1)] * len(planning_months(item))))))
            for power in group['powers'].values():
                power.update(share='100', factor='1', indoor_cost_percent='40', stock_indoor='100', stock_outdoor='100',
                             incoming_indoor='0', incoming_outdoor='0', prior_indoor='0', prior_outdoor='0')
        persist(item, self.actor, item.revision, state, 'Тестовые ручные данные')
        item.refresh_from_db()
        return item

    def test_real_month_power_and_block_details_conserve_financial_totals(self):
        item = self.complete()
        result = evaluate(item)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['totals']['quantity'], Decimal(30))
        self.assertEqual(result['totals']['cost'], Decimal(3000))
        self.assertEqual(result['totals']['revenue'], Decimal(4500))
        self.assertEqual(result['totals']['closing'], Decimal(70))
        self.assertEqual(sum(row['cost'] for row in result['shipments']), Decimal(3000))
        self.assertEqual(sum(row['revenue'] for row in result['shipments']), Decimal(4500))
        for kind in ('indoor', 'outdoor'):
            self.assertEqual(sum(row['quantity'] for row in result['shipments'] if row['kind'] == kind), Decimal(30))

    def test_missing_values_are_not_zero_and_approval_is_blocked(self):
        self.assertTrue(evaluate(self.scenario)['errors'])
        self.assertIsNone(evaluate(self.scenario)['totals']['closing'])
        with self.assertRaises(ValidationError):
            approve(self.scenario, self.actor, 1, False)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.status, 'draft')

    def test_revision_guard_and_approved_snapshot_are_immutable(self):
        item = self.complete()
        stale = SplitScenario.objects.get(pk=item.pk)
        persist(item, self.actor, item.revision, deepcopy(item.state), 'Изменение')
        with self.assertRaises(ValidationError):
            persist(stale, self.actor, stale.revision, {'corrupt': True}, 'Старая вкладка')
        item.refresh_from_db()
        approve(item, self.actor, item.revision, False)
        item.refresh_from_db()
        frozen = deepcopy(item.state)
        with self.assertRaises(ValidationError):
            persist(item, self.actor, item.revision, {}, 'Недопустимо')
        with self.assertRaises(ValidationError):
            item.save()
        self.models[0].name = 'Changed later'
        self.models[0].save()
        item.refresh_from_db()
        self.assertEqual(item.state, frozen)
        self.assertEqual(evaluate(item)['errors'], [])
        new = correction(item, self.actor)
        self.assertEqual(new.version, 2)
        self.assertEqual(new.parent_id, item.pk)
        self.assertEqual(new.state, frozen)
        self.assertEqual(correction(item, self.actor).pk, new.pk)
        changed = deepcopy(new.state)
        next(iter(next(iter(changed['groups'].values()))['channels'].values()))['input'] = '25'
        persist(new, self.actor, new.revision, changed, 'Корректировка объёма')
        item.refresh_from_db()
        self.assertEqual(item.state, frozen)

    def test_deficit_requires_explicit_acknowledgement_and_counts_both_blocks(self):
        item = self.complete()
        power = next(iter(next(iter(item.state['groups'].values()))['powers'].values()))
        power['stock_outdoor'] = '10'
        result = evaluate(item)
        self.assertEqual(result['totals']['closing'], Decimal(-20))
        self.assertTrue(result['warnings'])
        with self.assertRaises(ValidationError):
            approve(item, self.actor, item.revision, False)
        approve(item, self.actor, item.revision, True)
        item.refresh_from_db()
        self.assertEqual(item.status, 'approved')

    def test_annual_target_subtracts_actual_and_rejects_target_below_actual(self):
        item = self.complete()
        item.entry_mode = 'annual'
        line = next(iter(next(iter(item.state['groups'].values()))['channels'].values()))
        line['input'], line['fact'] = '50', '20'
        self.assertEqual(quantity(item, line), Decimal(30))
        line['fact'] = '60'
        self.assertTrue(evaluate(item)['errors'])

    def test_all_tabs_render_and_exports_contain_real_detail_and_escape_formula_text(self):
        item = self.complete()
        url = reverse('split_scenario', args=[item.pk])
        for tab in ('plan', 'distribution', 'stock', 'sources', 'checks', 'versions', 'detail'):
            with self.subTest(tab=tab):
                self.assertEqual(self.client.get(url, {'tab': tab}).status_code, 200)
        item.title = '=DANGEROUS()'
        item.save()
        response = self.client.get(url, {'export': 'xlsx'})
        book = load_workbook(BytesIO(b''.join(response.streaming_content)))
        self.assertEqual(book['Сценарий']['B1'].value, "'=DANGEROUS()")
        self.assertGreater(book['По моделям 1'].max_row, 1)
        book.close(); response.close()
        response = self.client.get(url, {'export': 'dwh'})
        self.assertContains(response, 'только для утверждённой')
        approve(item, self.actor, item.revision, False)
        csv_response = self.client.get(url, {'export': 'dwh'})
        self.assertEqual(csv_response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('m0', csv_response.content.decode('utf-8-sig'))

    def test_forecast_import_prorates_calendar_days_and_rejects_out_of_gap_interval(self):
        source = self.complete()
        approve(source, self.actor, source.revision, False)
        source.refresh_from_db()
        destination = create_scenario(self.actor, {'title': '2028', 'year': 2028, 'start': date(2028, 1, 1),
            'end': date(2028, 12, 31), 'stock_date': date(2027, 9, 30), 'entry_mode': 'period',
            'groups': [self.group], 'channels': self.channels})
        result = forecast_preview(destination, source, date(2027, 10, 16), date(2027, 10, 31))
        october = sum(row['quantity'] for row in evaluate(source)['shipments'] if row['month'] == '2027-10' and row['kind'] == 'indoor')
        self.assertEqual(result[0]['value'], (october * Decimal(16) / Decimal(31)).quantize(Decimal('.000001')))
        with self.assertRaises(ValidationError):
            forecast_preview(destination, source, date(2027, 9, 1), date(2027, 10, 31))

    def test_http_manual_full_cycle_and_scenario_creation(self):
        data = {'title': 'Создан через форму', 'start': '2027-12-01', 'end': '2027-12-31', 'stock_date': '2027-11-30'}
        response = self.client.post(reverse('split_scenarios'), data)
        self.assertEqual(response.status_code, 302)
        item = SplitScenario.objects.get(title='Создан через форму')
        self.assertEqual(item.state['groups'], {})
        self.assertEqual(item.state['channels'], {})
        gid, cid = str(self.group.pk), str(self.channels[0].pk)
        url = reverse('split_scenario', args=[item.pk])
        self.assertContains(self.client.get(url), 'Начать планирование')
        self.client.post(url, {'action': 'composition', 'confirm': 'on', 'revision': item.revision,
            'groups': [gid], 'channels': [cid], 'volume_mode': 'period'})
        def post(values):
            item.refresh_from_db()
            result = self.client.post(url, {'revision': item.revision, 'group': gid, 'channel': cid, **values})
            self.assertEqual(result.status_code, 302, result.content[:2000])
        post({'action': 'lines', 'ddp_' + gid: '100', 'q_' + gid + '_' + cid: '12', 'markup_' + gid + '_' + cid: '1.5'})
        post({'action': 'distribution', 'month_2027-12': '100', 'share_0': '100', 'factor_0': '1', 'cost_0': '40',
              'indoor_0': self.models[0].pk, 'outdoor_0': self.models[1].pk})
        post({'action': 'stock', 'buy': '0', 'stock_indoor_0': '20', 'stock_outdoor_0': '20',
              'incoming_indoor_0': '0', 'incoming_outdoor_0': '0'})
        item.refresh_from_db()
        self.assertEqual(evaluate(item)['errors'], [])
        post({'action': 'approve'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'approved')
        response = self.client.post(url, {'action': 'correction'})
        self.assertEqual(response.status_code, 302)

    def test_access_csrf_and_foreign_version_comparison(self):
        url = reverse('split_scenario', args=[self.scenario.pk])
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.actor)
        self.assertEqual(secure.post(url, {'action': 'approve'}).status_code, 403)
        foreign = create_scenario(self.actor, {'title': 'Other', 'year': 2027, 'start': date(2027, 1, 1), 'end': date(2027, 12, 31),
            'stock_date': date(2026, 12, 31), 'entry_mode': 'period', 'groups': [self.group], 'channels': self.channels})
        self.assertEqual(self.client.get(url, {'compare': foreign.pk}).status_code, 404)
        reader = User.objects.create_user('scenario-reader')
        self.client.force_login(reader)
        for target in (url, reverse('split_scenarios')):
            self.assertEqual(self.client.get(target).status_code, 403)
            self.assertEqual(self.client.post(target, {'action': 'correction'}).status_code, 403)

    def test_monthly_quantity_mode_preserves_manual_months_and_checks_total(self):
        item = self.complete()
        line = next(iter(next(iter(item.state['groups'].values()))['channels'].values()))
        line['allocation'] = 'quantity'
        line['month_quantities'] = {month: '0' for month in planning_months(item)}
        line['month_quantities']['2027-06'] = '10'
        result = evaluate(item)
        self.assertEqual(result['errors'], [])
        rows = [row for row in result['shipments'] if row['channel_id'] == str(self.channels[0].pk) and row['kind'] == 'indoor']
        self.assertEqual(sum(row['quantity'] for row in rows if row['month'] == '2027-06'), 10)
        self.assertEqual(sum(row['quantity'] for row in rows if row['month'] != '2027-06'), 0)
        line['month_quantities']['2027-06'] = '9'
        self.assertTrue(evaluate(item)['errors'])

    def test_composition_removal_and_restore_keep_line_values(self):
        from django.http import QueryDict
        item = self.complete()
        gid = next(iter(item.state['groups']))
        first, second = map(lambda channel: str(channel.pk), self.channels)
        post = QueryDict('', mutable=True)
        post.update(action='composition', confirm='on')
        post.setlist('groups', [gid]); post.setlist('channels', [second])
        state = changed_state(item, post)
        self.assertNotIn(first, state['channels'])
        self.assertEqual(state['groups'][gid]['archived_lines'][first]['input'], '10')
        item.state = state
        post.setlist('channels', [first, second])
        restored = changed_state(item, post)
        self.assertEqual(restored['groups'][gid]['channels'][first]['input'], '10')

    def test_volume_mode_is_selected_inside_planning_and_uses_saved_fact(self):
        from django.http import QueryDict
        item = self.complete()
        gid, cid = next(iter(item.state['groups'])), str(self.channels[0].pk)
        post = QueryDict('', mutable=True)
        post.update(action='composition', confirm='on', volume_mode='annual')
        post.setlist('groups', [gid]); post.setlist('channels', list(item.state['channels']))
        state = changed_state(item, post)
        state['groups'][gid]['channels'][cid]['input'] = '50'
        state['groups'][gid]['channels'][cid]['fact'] = '20'
        persist(item, self.actor, item.revision, state, 'Способ объёма на этапе планирования')
        item.refresh_from_db()
        self.assertEqual(quantity(item, item.state['groups'][gid]['channels'][cid]), 30)
        response = self.client.get(reverse('split_scenario', args=[item.pk]))
        self.assertContains(response, 'Годовая цель')

    def test_bulk_keeps_locked_values_and_preview_does_not_write(self):
        item = self.complete()
        group = next(iter(item.state['groups'].values()))
        cid = str(self.channels[0].pk)
        group['channels'][cid]['lock_qty'] = True
        group['channels'][cid]['lock_markup'] = True
        with self.assertRaises(ValidationError):
            changed_state(item, {'action': 'bulk', 'channel': cid, 'brand': 'Brand', 'quantity': '11', 'markup': '1.5'})
        candidate = changed_state(item, {'action': 'bulk', 'channel': cid, 'brand': 'Brand', 'quantity': '10', 'markup': '1.5'})
        self.assertEqual(next(iter(candidate['groups'].values()))['channels'][cid]['input'], '10')
        revision = item.revision
        url = reverse('split_scenario', args=[item.pk])
        response = self.client.post(url, {'action': 'bulk_preview', 'channel': cid, 'brand': 'Brand', 'quantity': '20', 'markup': '2'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Предпросмотр корректировки')
        item.refresh_from_db()
        self.assertEqual(item.revision, revision)

    def test_reference_refresh_preserves_inputs_and_marks_removed_models_for_review(self):
        item = self.complete()
        group = next(iter(item.state['groups'].values()))
        old_input = deepcopy(group['channels'])
        self.models[0].is_active = False
        self.models[0].save()
        state = changed_state(item, {'action': 'refresh', 'confirm': 'on'})
        updated = next(iter(state['groups'].values()))
        self.assertEqual(updated['channels'], old_input)
        self.assertEqual(updated['ddp'], '100')
        item.state = state
        self.assertTrue(evaluate(item)['errors'])

    def test_bulk_distributes_only_unlocked_quantity_and_markup(self):
        from .split_workflow import bulk_change
        other_models = [SalesPlanningGoods.objects.create(source_key='second' + str(index), name='Second ' + str(index),
            planning_group='G2', product_group='Бытовые кондиционеры', brand='Brand', power_class='07', block_type=kind)
            for index, kind in enumerate(['Внутренний', 'Наружный'])]
        rebuild_groups()
        other = SalesPlanningGroup.objects.get(name='G2')
        save_pair(other, '07', other_models[0].pk, other_models[1].pk)
        item = create_scenario(self.actor, {'title': 'Bulk', 'year': 2027, 'start': date(2027,1,1), 'end': date(2027,12,31),
            'stock_date': date(2026,12,31), 'entry_mode': 'period', 'groups': [self.group, other], 'channels': self.channels[:1]})
        self.complete(item)
        cid = str(self.channels[0].pk)
        locked, free = item.state['groups'][str(self.group.pk)], item.state['groups'][str(other.pk)]
        locked['channels'][cid].update(lock_qty=True, lock_markup=True, markup='2')
        free['ddp'] = '200'
        original = deepcopy(item.state)
        candidate = changed_state(item, {'action':'bulk', 'channel':cid, 'brand':'Brand', 'quantity':'40', 'markup':'2.5'})
        self.assertEqual(item.state, original)
        self.assertEqual(candidate['groups'][str(self.group.pk)]['channels'][cid]['input'], '10')
        self.assertEqual(candidate['groups'][str(self.group.pk)]['channels'][cid]['markup'], '2')
        self.assertEqual(Decimal(candidate['groups'][str(other.pk)]['channels'][cid]['input']), 30)
        item.state = candidate
        result = evaluate(item)
        self.assertEqual(result['totals']['quantity'], 40)
        self.assertLess(abs(result['totals']['revenue'] - Decimal(17500)), Decimal('.01'))

    def test_imported_partial_forecast_requires_completion_before_approval(self):
        source = self.complete()
        approve(source, self.actor, source.revision, False)
        source.refresh_from_db()
        destination = create_scenario(self.actor, {'title':'Next', 'year':2028, 'start':date(2028,1,1), 'end':date(2028,12,31),
            'stock_date':date(2027,9,30), 'entry_mode':'period', 'groups':[self.group], 'channels':self.channels})
        self.complete(destination)
        group = next(iter(destination.state['groups'].values()))
        group['forecast'] = {'2027-10':'1', '2027-11':'0', '2027-12':'0'}
        group['forecast_imports'] = {'2027-10': {'start':'2027-10-16', 'end':'2027-10-31', 'source':source.pk}}
        self.assertTrue(any('фрагмент' in message for message in evaluate(destination)['errors']))
        group['forecast_imports'] = {}
        self.assertEqual(evaluate(destination)['errors'], [])

    def test_tiny_allocations_never_charge_zero_quantity_and_stock_matches_model_plan(self):
        item = self.complete()
        group = next(iter(item.state['groups'].values()))
        group['ddp'] = '1000000'
        original = group['powers']['07']
        original['share'] = '50'
        group['powers']['09'] = deepcopy(original)
        for line in group['channels'].values():
            line['input'] = '.000001'
        result = evaluate(item)
        self.assertEqual(result['errors'], [])
        for row in result['shipments']:
            if row['quantity'] == 0:
                self.assertEqual(row['cost'], 0)
                self.assertEqual(row['revenue'], 0)
        for stock in result['stock']:
            sold = sum(row['quantity'] for row in result['shipments'] if row['power'] == stock['power'] and row['kind'] == 'indoor')
            self.assertEqual(stock['quantity'], sold)

