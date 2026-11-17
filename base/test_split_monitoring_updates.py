from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch,Mock
from django.contrib.auth.models import User
from django.test import TestCase,Client,RequestFactory
from django.urls import reverse
from .access import ADMIN,ANALYST
from .models import UserProjectAccess
from .split_monitoring_views import admin_toolbar,_forward
from scripts.split_monitoring_update import write


class MonitoringUpdateAccessTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('monitor-updater');self.client.force_login(self.user)
        self.url=reverse('split_monitoring_updates')
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.config=Path(self.temp.name)/'runtime.json'
        self.override=self.settings(SPLIT_MONITORING_CONFIG=self.config);self.override.enable();self.addCleanup(self.override.disable)

    def grant(self,role):UserProjectAccess.objects.update_or_create(user=self.user,project='split_monitoring',defaults={'role':role})

    def test_admin_only_get_post_and_status(self):
        with patch('base.split_monitoring_update_views.start') as worker:
            self.assertEqual(self.client.get(self.url).status_code,403)
            self.grant(ANALYST)
            self.assertEqual(self.client.get(self.url).status_code,403)
            self.assertEqual(self.client.get(self.url+'?status=1').status_code,403)
            self.assertEqual(self.client.post(self.url).status_code,403)
            worker.assert_not_called()
            self.grant(ADMIN)
            self.assertContains(self.client.get(self.url),'Обновить из GitHub')
            worker.return_value={'id':'a'*32}
            response=self.client.post(self.url,{'repository':'https://untrusted.example/project','branch':'other'})
            self.assertEqual(response.status_code,302)
            worker.assert_called_once_with(self.config,self.user.pk)
            self.client.logout();self.assertEqual(self.client.get(self.url).status_code,302)

    def test_post_requires_csrf(self):
        self.grant(ADMIN);client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        with patch('base.split_monitoring_update_views.start') as worker:
            self.assertEqual(client.post(self.url).status_code,403)
            page=client.get(self.url);self.assertEqual(page.status_code,200)
            worker.return_value={'id':'b'*32}
            self.assertEqual(client.post(self.url,{'csrfmiddlewaretoken':client.cookies['csrftoken'].value}).status_code,302)
            worker.assert_called_once()

    def test_status_does_not_expose_config_or_worker_fields(self):
        self.grant(ADMIN)
        record={'id':'a'*32,'state':'completed','stage':'Готово','steps':[],'message':'Готово','control_token':'secret','app_dir':'private-path','actor':self.user.pk}
        write(self.config.parent/'updates'/record['id']/'status.json',record)
        response=self.client.get(self.url+'?status=1&run='+record['id'])
        self.assertEqual(response.status_code,200)
        self.assertNotIn(b'secret',response.content);self.assertNotIn(b'private-path',response.content)
        self.assertEqual(self.client.get(self.url+'?status=1&run=../runtime').status_code,404)

    def test_toolbar_only_admin_and_real_body_tag(self):
        request=RequestFactory().get('/split-monitoring/');request.user=self.user
        body=b'<html><head><script>let text="<body>";</script></head><body class="native">Original</body></html>'
        self.grant(ANALYST);self.assertEqual(admin_toolbar(request,body),body)
        self.grant(ADMIN);request.user=User.objects.get(pk=self.user.pk)
        actual=admin_toolbar(request,body)
        self.assertIn(b'<body class="native"><nav',actual)
        self.assertIn('Обновить из GitHub'.encode(),actual);self.assertIn(b'csrfmiddlewaretoken',actual)
        request.portal_preview_user=self.user;self.assertEqual(admin_toolbar(request,body),body)
        from .split_monitoring_update_views import updates
        self.assertEqual(updates(request).status_code,403)

    def test_injected_page_length_is_updated(self):
        self.grant(ADMIN);request=RequestFactory().get('/split-monitoring/');request.user=self.user
        upstream=Mock();upstream.status=200;body=b'<html><body>native</body></html>';upstream.read.return_value=body
        upstream.getheader.side_effect=lambda name,default=None:{'Content-Type':'text/html; charset=utf-8','Content-Length':str(len(body)),'ETag':'old'}.get(name,default)
        connection=Mock();connection.getresponse.return_value=upstream
        with patch('base.split_monitoring_views.ensure_running',return_value={'port':8767}),patch('base.split_monitoring_views.http.client.HTTPConnection',return_value=connection):
            response=_forward(request,'/')
        self.assertEqual(int(response['Content-Length']),len(response.content));self.assertNotIn('ETag',response)
        self.assertIn('Обновить из GitHub'.encode(),response.content)
