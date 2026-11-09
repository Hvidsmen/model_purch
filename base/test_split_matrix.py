from copy import deepcopy
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle
from .models import SplitScenario
from .split_workflow import approve


class SplitMatrixTests(TestCase):
    def setUp(self):
        test_split_cycle.SplitCycleTests.setUp(self)
        test_split_cycle.SplitCycleTests.complete(self)
        self.url = reverse('split_scenario', args=[self.scenario.pk])
        self.gid = str(self.group.pk)
        self.cid = str(self.channels[0].pk)

    def inputs(self, action):
        item = SplitScenario.objects.get(pk=self.scenario.pk)
        data = {'action': action, 'revision': item.revision, 'with_lines':'1'}
        for gid, group in item.state['groups'].items():
            data['ddp_' + gid] = group['ddp'] or ''
            for cid, line in group['channels'].items():
                suffix = gid + '_' + cid
                data.update({'q_' + suffix:line['input'], 'fact_' + suffix:line['fact'] or '', 'markup_' + suffix:line['markup'] or ''})
                if line['lock_qty']: data['lockq_' + suffix] = 'on'
                if line['lock_markup']: data['lockm_' + suffix] = 'on'
        return data

    def test_channels_are_headers_and_groups_are_rows(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-plan-group="' + self.gid + '"', count=1)
        for channel in self.channels:
            self.assertContains(response, 'data-plan-channel="' + str(channel.pk) + '"', count=1)
            self.assertContains(response, 'value="remove_channel:' + str(channel.pk) + '"')
        self.assertContains(response, '+ Добавить группу')
        self.assertContains(response, '+ Добавить канал')

    def test_remove_and_restore_channel_preserve_current_unsaved_inputs(self):
        post = self.inputs('remove_channel:' + self.cid)
        suffix = self.gid + '_' + self.cid
        post['q_' + suffix] = '33'
        post['lockq_' + suffix] = 'on'
        self.assertEqual(self.client.post(self.url, post).status_code, 302)
        self.scenario.refresh_from_db()
        archive = self.scenario.state['groups'][self.gid]['archived_lines'][self.cid]
        self.assertEqual(archive['input'], '33')
        self.assertTrue(archive['lock_qty'])
        self.assertNotIn(self.cid, self.scenario.state['channels'])
        post = self.inputs('add_channel')
        post['new_channel'] = self.cid
        self.assertEqual(self.client.post(self.url, post).status_code, 302)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['channels'][self.cid]['input'], '33')

    def test_remove_last_group_then_last_channel_and_restore_them(self):
        self.assertEqual(self.client.post(self.url, self.inputs('remove_group:' + self.gid)).status_code, 302)
        for channel in self.channels:
            self.assertEqual(self.client.post(self.url, self.inputs('remove_channel:' + str(channel.pk))).status_code, 302)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'], {})
        self.assertEqual(self.scenario.state['channels'], {})
        self.assertEqual(self.client.get(self.url).status_code, 200)
        post = self.inputs('add_group'); post['new_group'] = self.gid
        self.assertEqual(self.client.post(self.url, post).status_code, 302)
        post = self.inputs('add_channel'); post['new_channel'] = self.cid
        self.assertEqual(self.client.post(self.url, post).status_code, 302)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['channels'][self.cid]['input'], '10')

    def test_stale_approved_and_invalid_changes_do_not_modify_plan(self):
        original = deepcopy(self.scenario.state)
        post = self.inputs('add_channel'); post['new_channel'] = '999999'
        self.assertEqual(self.client.post(self.url, post).status_code, 200)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state, original)
        post = self.inputs('remove_group:' + self.gid); post['revision'] = 0
        self.client.post(self.url, post)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state, original)
        approve(self.scenario, self.actor, self.scenario.revision, False)
        self.scenario.refresh_from_db()
        self.client.post(self.url, self.inputs('remove_group:' + self.gid))
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state, original)
