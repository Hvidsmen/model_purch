import json
from unittest.mock import Mock,patch
from django.contrib.auth.models import User,Group
from django.http import HttpResponse
from django.test import TestCase,Client,RequestFactory
from django.urls import reverse
from .access import ADMIN,ANALYST,can_access
from .models import UserProjectAccess,GroupProjectAccess


class SplitMonitoringAccessTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('split-user');self.client.force_login(self.user)
        self.url=reverse('split_monitoring_index');self.api='/card-api/'

    def grant(self,role=ANALYST):UserProjectAccess.objects.update_or_create(user=self.user,project='split_monitoring',defaults={'role':role})

    def test_anonymous_and_ungranted_cannot_read_app_or_snapshots(self):
        with patch('base.split_monitoring_views.forward') as backend:
            self.assertEqual(self.client.get(self.url).status_code,403)
            self.assertEqual(self.client.get('/card-api/meta').status_code,403)
            self.assertEqual(self.client.get(self.url+'snapshot.'+'a'*64+'.json').status_code,403)
            self.client.logout()
            self.assertEqual(self.client.get(self.url).status_code,302)
            self.assertEqual(self.client.get('/card-api/meta').status_code,401)
            backend.assert_not_called()

    def test_grants_are_separate_from_legacy_analyst_and_personal_deny_wins(self):
        group,_=Group.objects.get_or_create(name=ANALYST);self.user.groups.add(group)
        self.assertFalse(can_access(self.user,'split_monitoring'))
        GroupProjectAccess.objects.create(group=group,project='split_monitoring',role=ANALYST)
        self.assertTrue(can_access(self.user,'split_monitoring'))
        self.grant('');self.assertFalse(can_access(self.user,'split_monitoring'))

    def test_fifth_link_and_project_forms_are_available(self):
        from .user_views import UserForm
        from .group_views import ReportGroupForm
        from .admin import PortalUserChangeForm,PortalUserCreationForm
        self.grant()
        page=self.client.get(reverse('index_base'))
        self.assertContains(page,'Мониторинг сплитов')
        self.assertContains(page,'href="/split-monitoring/" target="_blank" rel="noopener"')
        for form in (UserForm,ReportGroupForm,PortalUserChangeForm,PortalUserCreationForm):
            self.assertIn('split_monitoring_role',form.base_fields)

    def test_group_editor_renders_and_saves_monitoring_grant_and_deny(self):
        actor=User.objects.create_superuser('split-groups-admin');self.client.force_login(actor)
        url=reverse('portal_user_groups')
        response=self.client.get(url)
        self.assertContains(response,'name="split_monitoring_role"')
        data={'name':'Split report readers','motivation_role':'','purchases_role':'','dashboards_role':'',
              'split_monitoring_role':ANALYST,'members':[self.user.pk]}
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        group=Group.objects.get(name='Split report readers')
        self.assertEqual(GroupProjectAccess.objects.get(group=group,project='split_monitoring').role,ANALYST)
        self.assertTrue(can_access(User.objects.get(pk=self.user.pk),'split_monitoring'))
        response=self.client.get(url,{'edit':group.pk})
        self.assertContains(response,'name="split_monitoring_role"')
        self.assertEqual(response.context['form']['split_monitoring_role'].value(),ANALYST)
        response=self.client.post(url,{**data,'group':group.pk,'split_monitoring_role':''})
        self.assertEqual(response.status_code,302)
        self.assertFalse(can_access(User.objects.get(pk=self.user.pk),'split_monitoring'))

    def test_json_card_uses_exact_origin_and_does_not_require_django_token(self):
        self.grant();client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        with patch('base.split_monitoring_views.forward',return_value=JsonResponseForTest()) as backend:
            for origin in ('http://evil.example','null',''):
                self.assertEqual(client.post('/card-api/card',{},content_type='application/json',HTTP_ORIGIN=origin).status_code,403)
            response=client.post('/card-api/card',{},content_type='application/json',HTTP_ORIGIN='http://testserver')
            self.assertEqual(response.status_code,200)
            backend.assert_called_once()

    def test_only_project_admin_refreshes_and_control_paths_are_not_proxied(self):
        self.grant()
        with patch('base.split_monitoring_views.forward',return_value=JsonResponseForTest()) as backend:
            self.assertEqual(self.client.post('/card-api/refresh',{},content_type='application/json',HTTP_ORIGIN='http://testserver').status_code,403)
            self.assertEqual(self.client.post('/card-api/__portal_control/stop',{},content_type='application/json',HTTP_ORIGIN='http://testserver').status_code,404)
            backend.assert_not_called()
            self.grant(ADMIN)
            self.assertEqual(self.client.post('/card-api/refresh',{},content_type='application/json',HTTP_ORIGIN='http://testserver').status_code,200)
            self.assertEqual(self.client.post(self.url+'card-api/refresh',{},content_type='application/json',HTTP_ORIGIN='http://testserver').status_code,405)

    def test_invalid_json_and_large_or_non_json_bodies_are_rejected(self):
        self.grant()
        with patch('base.split_monitoring_views.forward') as backend:
            self.assertEqual(self.client.post('/card-api/card','invalid',content_type='application/json',HTTP_ORIGIN='http://testserver').status_code,400)
            self.assertEqual(self.client.post('/card-api/card','[]',content_type='application/json',HTTP_ORIGIN='http://testserver').status_code,400)
            self.assertEqual(self.client.post('/card-api/card','x'*20001,content_type='application/json',HTTP_ORIGIN='http://testserver').status_code,413)
            self.assertEqual(self.client.post('/card-api/card','x',content_type='text/plain',HTTP_ORIGIN='http://testserver').status_code,415)
            backend.assert_not_called()

    def test_preview_can_calculate_but_never_refresh(self):
        from .preview import SESSION_KEY
        self.grant(ADMIN)
        actor=User.objects.create_superuser('split-preview-admin')
        self.client.force_login(actor)
        session=self.client.session;session[SESSION_KEY]={'actor':str(actor.pk),'target':self.user.pk};session.save()
        with patch('base.split_monitoring_views.forward',return_value=JsonResponseForTest()) as backend:
            self.assertEqual(self.client.post('/card-api/card',{},content_type='application/json',HTTP_ORIGIN='http://testserver').status_code,200)
            self.assertEqual(self.client.post('/card-api/refresh',{},content_type='application/json',HTTP_ORIGIN='http://testserver').status_code,403)
            backend.assert_called_once()

    def test_gateway_drops_cookies_and_control_headers(self):
        from .split_monitoring_views import forward
        request=RequestFactory().post('/card-api/card','{}',content_type='application/json',HTTP_ORIGIN='http://testserver',HTTP_COOKIE='secret',HTTP_X_PORTAL_CONTROL='spoofed')
        upstream=Mock(status=200);upstream.read.return_value=b'{}'
        upstream.getheader.side_effect=lambda key,default=None:{'Content-Type':'application/json','Set-Cookie':'bad=1'}.get(key,default)
        conn=Mock();conn.getresponse.return_value=upstream
        with patch('base.split_monitoring_views.ensure_running',return_value={'port':8767}),patch('base.split_monitoring_views.http.client.HTTPConnection',return_value=conn) as connection:
            response=forward(request,'/card-api/card')
        self.assertEqual(connection.call_args.args[:2],('127.0.0.1',8767))
        headers=conn.request.call_args.kwargs['headers']
        self.assertNotIn('Cookie',headers);self.assertNotIn('X-Portal-Control',headers)
        self.assertNotIn('Set-Cookie',response);self.assertIn('private',response['Cache-Control'])


def JsonResponseForTest():return HttpResponse('{}',content_type='application/json')
