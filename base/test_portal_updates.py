import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from base.testing import authorize_admin
from base.models import UserProjectAccess
from base.access import ADMIN, MODELER
from scripts import portal_update_state as state


class PortalUpdatesTests(TestCase):
    def setUp(self):
        self.actor = authorize_admin(self.client)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.override = override_settings(BASE_DIR=self.root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        state.write(self.root / '.local' / 'browser-updates.json', {'root': str(self.root), 'task_name': 'Portal-Updater', 'portal_task_name': 'Portal'})
        self.windows = patch('base.portal_updates.windows_supported', return_value=True)
        self.windows.start()
        self.addCleanup(self.windows.stop)

    def test_global_admin_only_and_csrf_required(self):
        for role in (ADMIN, MODELER):
            user = User.objects.create_user('project-' + role)
            UserProjectAccess.objects.create(user=user, project='motivation', role=role)
            client = Client()
            client.force_login(user)
            for route in ('portal_updates', 'portal_update_start'):
                response = client.get(reverse(route)) if route == 'portal_updates' else client.post(reverse(route), '{}', content_type='application/json')
                self.assertEqual(response.status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        self.assertEqual(client.post(reverse('portal_update_start'), '{}', content_type='application/json').status_code, 403)

    def test_two_phase_dispatch_stores_tokens_before_the_task_can_stop_django(self):
        with patch('base.portal_updates.subprocess.run') as command:
            response = self.client.post(reverse('portal_update_start'), {'confirm': True}, content_type='application/json')
            self.assertEqual(response.status_code, 201)
            command.assert_not_called()
            data = response.json()
            self.assertFalse(state.read(state.run_directory(self.root, data['run']['id']) / 'request.json')['armed'])
            run_id = data['run']['id']
            self.assertNotIn(data['token'], json.dumps(state.state(self.root, run_id)))
            response = self.client.post(reverse('portal_update_launch', args=[run_id]))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(state.read(state.run_directory(self.root, run_id) / 'request.json')['armed'])
            self.assertIn('portal_update_task.ps1', command.call_args.args[0][6])
        status_url = reverse('portal_update_status', args=[run_id])
        anonymous = Client()
        self.assertEqual(anonymous.get(status_url).status_code, 403)
        response = anonymous.get(status_url, HTTP_X_PORTAL_UPDATE_STATUS=data['token'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], run_id)
        self.assertEqual(self.client.post(reverse('portal_update_start'), {'confirm': True}, content_type='application/json').status_code, 409)

    def test_missing_confirmation_and_launch_failure_do_not_stop_portal(self):
        response = self.client.post(reverse('portal_update_start'), {}, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        data = self.client.post(reverse('portal_update_start'), {'confirm': True}, content_type='application/json').json()
        with patch('base.portal_updates.subprocess.run', side_effect=OSError('sensitive system details')):
            response = self.client.post(reverse('portal_update_launch', args=[data['run']['id']]))
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('sensitive', response.content.decode())
        self.assertEqual(state.state(self.root, data['run']['id'])['status'], 'failed')
        self.assertEqual(self.client.get(reverse('index_base')).status_code, 200)

    def test_page_health_and_queued_credential_recovery(self):
        self.assertContains(self.client.get(reverse('portal_updates')), 'Обновить портал')
        current, token, control = state.create(self.root, self.actor.pk, 'Administrator')
        response = self.client.post(reverse('portal_update_credentials', args=[current['id']]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(state.authorized(self.root, current['id'], token))
        self.assertTrue(state.authorized(self.root, current['id'], response.json()['token']))
        state.update(self.root, current['id'], status='running')
        self.assertEqual(self.client.post(reverse('portal_update_credentials', args=[current['id']])).status_code, 409)
        with patch('base.portal_updates.revision', return_value='a' * 40):
            response = Client().get(reverse('portal_update_health'))
        self.assertEqual(response.json(), {'ready': True, 'revision': 'a' * 40})
