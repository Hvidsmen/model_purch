"""Archive controls list visibility without changing saved financial results."""
from io import BytesIO
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase,Client
from django.urls import reverse
from openpyxl import load_workbook
from base.access import MOTIVATION_VIEWER
from base.models import UserProjectAccess
from . import test_shareholders as fixtures
from .models import ShareholderCalculation


class ShareholderArchiveTests(TestCase):
    setUp=fixtures.SnapshotTests.setUp
    report_url=fixtures.SnapshotTests.url

    def list_url(self,archived=False):
        return reverse('motivation_shareholders')+('?archive=1' if archived else '')

    def action_url(self):
        return reverse('motivation_shareholder_archive',args=[self.run.pk])

    def test_archive_hides_run_and_preserves_state_parameters_and_snapshots(self):
        before=ShareholderCalculation.objects.values().get(pk=self.run.pk)
        chunks=list(self.run.chunks.values())
        self.assertContains(self.client.get(self.list_url()),str(self.run.pk))
        with patch('admin_motivation.services.shareholder_runner.launch') as launch:
            response=self.client.post(self.action_url(),{'action':'archive'})
        self.assertRedirects(response,self.list_url())
        self.assertNotContains(self.client.get(self.list_url()),str(self.run.pk))
        archived=self.client.get(self.list_url(True))
        self.assertContains(archived,str(self.run.pk))
        self.assertContains(archived,'Вернуть из архива')
        self.run.refresh_from_db();self.assertTrue(self.run.is_archived)
        after=ShareholderCalculation.objects.values().get(pk=self.run.pk)
        self.assertEqual({k:v for k,v in after.items() if k!='is_archived'},
                         {k:v for k,v in before.items() if k!='is_archived'})
        self.assertEqual(list(self.run.chunks.values()),chunks)
        launch.assert_not_called()

    def test_restore_returns_run_to_default_list(self):
        self.run.is_archived=True;self.run.save(update_fields=['is_archived'])
        response=self.client.post(self.action_url(),{'action':'restore','archive':'1'})
        self.assertRedirects(response,self.list_url(True))
        self.run.refresh_from_db();self.assertFalse(self.run.is_archived)
        self.assertContains(self.client.get(self.list_url()),str(self.run.pk))
        self.assertNotContains(self.client.get(self.list_url(True)),str(self.run.pk))

    def test_archived_report_and_excel_remain_accessible_at_same_urls(self):
        query={'type':'Политики','author':'Иванов','period':['1','5']}
        before=self.client.get(self.report_url('team'),query).context['ledger']['total']
        self.client.post(self.action_url(),{'action':'archive'})
        response=self.client.get(self.report_url('team'),query)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['ledger']['total'],before)
        exported=self.client.get(self.report_url('team'),{**query,'export':'xlsx'})
        self.assertEqual(exported.status_code,200)
        book=load_workbook(BytesIO(b''.join(exported.streaming_content)))
        self.assertIn('Параметры',book.sheetnames);book.close();exported.close()

    def test_all_states_can_be_hidden_without_cancelling_or_changing_worker_state(self):
        for state in ('queued','running','failed','completed'):
            self.run.state=state;self.run.save(update_fields=['state'])
            for action in ('archive','archive','restore','restore'):
                self.assertEqual(self.client.post(self.action_url(),{'action':action}).status_code,302)
                self.run.refresh_from_db()
                self.assertEqual(self.run.state,state)
                self.assertEqual(self.run.is_archived,action=='archive')

    def test_post_only_with_csrf_and_valid_action(self):
        self.assertEqual(self.client.get(self.action_url()).status_code,405)
        self.assertEqual(self.client.post(self.action_url(),{'action':'delete'}).status_code,400)
        self.assertEqual(self.client.post(self.action_url(),{}).status_code,400)
        csrf_client=Client(enforce_csrf_checks=True);csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.action_url(),{'action':'archive'}).status_code,403)
        self.run.refresh_from_db();self.assertFalse(self.run.is_archived)

    def test_readonly_role_sees_archive_but_cannot_change_visibility(self):
        user=User.objects.create_user('shareholder-viewer')
        UserProjectAccess.objects.create(user=user,project='motivation',role=MOTIVATION_VIEWER)
        self.client.force_login(user)
        self.run.is_archived=True;self.run.save(update_fields=['is_archived'])
        response=self.client.get(self.list_url(True))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,str(self.run.pk))
        self.assertNotContains(response,'Вернуть из архива')
        self.assertEqual(self.client.post(self.action_url(),{'action':'restore'}).status_code,403)
        self.run.refresh_from_db();self.assertTrue(self.run.is_archived)

    def test_pagination_preserves_archive_filter(self):
        originals=ShareholderCalculation.objects.get(pk=self.run.pk)
        for index in range(51):
            ShareholderCalculation.objects.create(name=f'Archived {index}',as_of=originals.as_of,
                scenario='combined',source_job=self.job,created_by=self.user,is_archived=True)
        first=self.client.get(self.list_url(True))
        second=self.client.get(self.list_url(True)+'&page=2')
        self.assertEqual(len(first.context['runs']),50)
        self.assertEqual(len(second.context['runs']),1)
        self.assertContains(first,'?archive=1&amp;page=2')
        self.assertEqual(self.client.get(self.list_url()).context['runs'].paginator.count,1)
