import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command,CommandError
from django.test import TestCase,SimpleTestCase,Client,RequestFactory,override_settings
from django.urls import reverse
from portal.deployment import da_dev_profile,site_settings
from base.models import UserProjectAccess
from base.access import ANALYST
from base.proxy import TrustedProxyMiddleware

PROXY=dict(ALLOWED_HOSTS=['testserver','192.168.221.178','da-dev.daichi.ru'],
           SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https'),USE_X_FORWARDED_HOST=True,USE_X_FORWARDED_PORT=True,
           PORTAL_TRUSTED_PROXIES=('192.168.221.9',),CSRF_TRUSTED_ORIGINS=['https://da-dev.daichi.ru'],
           SESSION_COOKIE_SECURE=True,CSRF_COOKIE_SECURE=True,AD_AUTH_ENABLED=True,AD_AUTH_METHOD='ldaps',
           AD_SHORT_NAMES=True,AD_REQUIRE_HTTPS=True,AD_NETBIOS_DOMAIN='TT',AD_UPN_SUFFIX='thermotrade.local',
           AUTHENTICATION_BACKENDS=['base.ad_auth.ActiveDirectoryBackend','django.contrib.auth.backends.ModelBackend'])
HEADERS=dict(REMOTE_ADDR='192.168.221.9',HTTP_HOST='192.168.221.178:8000',HTTP_X_FORWARDED_HOST='da-dev.daichi.ru',
             HTTP_X_FORWARDED_PROTO='https',HTTP_X_FORWARDED_PORT='443')


@override_settings(**PROXY)
class HaproxyLoginTests(TestCase):
    def test_proxy_https_short_tt_login_reuses_identity_roles_and_csrf(self):
        user=get_user_model().objects.create_user('TT\\Alice')
        UserProjectAccess.objects.create(user=user,project='split_monitoring',role=ANALYST)
        client=Client(enforce_csrf_checks=True)
        page=client.get(reverse('portal_login'),**HEADERS)
        self.assertContains(page,'логин без домена')
        self.assertTrue(client.cookies['csrftoken']['secure'])
        with patch('base.ad_auth.directory_identity',return_value={'username':'tt\\alice'}) as directory:
            response=client.post(reverse('portal_login'),{'username':'alice','password':'not-saved','csrfmiddlewaretoken':client.cookies['csrftoken'].value},HTTP_ORIGIN='https://da-dev.daichi.ru',**HEADERS)
        self.assertEqual(response.status_code,302)
        directory.assert_called_once_with('TT\\alice','not-saved')
        self.assertEqual(client.session['_auth_user_id'],str(user.pk))
        self.assertTrue(client.cookies['sessionid']['secure'])
        self.assertEqual(get_user_model().objects.filter(username__iexact='tt\\alice').count(),1)
        self.assertEqual(UserProjectAccess.objects.get(user=user,project='split_monitoring').role,ANALYST)
        self.assertNotIn('not-saved',str(dict(client.session)))

    def test_untrusted_forwarded_headers_cannot_authorize_http_password_login(self):
        headers={**HEADERS,'REMOTE_ADDR':'192.168.221.100'}
        with patch('base.ad_auth.directory_identity') as directory:
            response=self.client.post(reverse('portal_login'),{'username':'alice','password':'never-send'},**headers)
        self.assertEqual(response.status_code,200);directory.assert_not_called()
        self.assertContains(response,'не вводите доменный пароль')

    def test_existing_local_password_account_keeps_its_identity(self):
        user=get_user_model().objects.create_user('local-admin',password='local-password')
        with patch('base.ad_auth.directory_identity') as directory:
            response=self.client.post(reverse('portal_login'),{'username':'local-admin','password':'local-password'},**HEADERS)
        self.assertEqual(response.status_code,302);directory.assert_not_called()
        self.assertEqual(self.client.session['_auth_user_id'],str(user.pk))

    def test_failed_short_name_never_falls_back_to_local_domain_password(self):
        get_user_model().objects.create_user('tt\\alice',password='old-local-password')
        with patch('base.ad_auth.directory_identity',side_effect=RuntimeError('Unavailable')):
            response=self.client.post(reverse('portal_login'),{'username':'alice','password':'old-local-password'},**HEADERS)
        self.assertEqual(response.status_code,200);self.assertNotIn('_auth_user_id',self.client.session)

    def test_monitor_json_origin_uses_external_https_host(self):
        user=get_user_model().objects.create_superuser('proxy-admin');self.client.force_login(user)
        from django.http import JsonResponse
        with patch('base.split_monitoring_views.forward',return_value=JsonResponse({'ok':True})) as forward:
            response=self.client.post('/card-api/card','{}',content_type='application/json',HTTP_ORIGIN='https://da-dev.daichi.ru',**HEADERS)
        self.assertEqual(response.status_code,200);forward.assert_called_once()

    def test_csrf_cookie_and_session_names_are_independent_and_bad_origin_is_denied(self):
        with self.settings(SESSION_COOKIE_NAME='da_portal_sessionid',CSRF_COOKIE_NAME='da_portal_csrftoken'):
            client=Client(enforce_csrf_checks=True)
            client.get(reverse('portal_login'),**HEADERS)
            self.assertIn('da_portal_csrftoken',client.cookies)
            with patch('base.ad_auth.directory_identity') as directory:
                response=client.post(reverse('portal_login'),{'username':'alice','password':'secret','csrfmiddlewaretoken':client.cookies['da_portal_csrftoken'].value},HTTP_ORIGIN='https://evil.example',**HEADERS)
            self.assertEqual(response.status_code,403);directory.assert_not_called()

    def test_missing_csrf_still_refused_behind_proxy(self):
        client=Client(enforce_csrf_checks=True)
        with patch('base.ad_auth.directory_identity') as directory:
            response=client.post(reverse('portal_login'),{'username':'alice','password':'secret'},HTTP_ORIGIN='https://da-dev.daichi.ru',**HEADERS)
        self.assertEqual(response.status_code,403);directory.assert_not_called()


@override_settings(PORTAL_TRUSTED_PROXIES=('192.168.221.9',))
class DeploymentProfileTests(SimpleTestCase):
    def test_peer_is_transport_address_not_xff_and_untrusted_headers_are_removed(self):
        request=RequestFactory().get('/',REMOTE_ADDR='192.168.221.100',HTTP_X_FORWARDED_FOR='192.168.221.9',HTTP_X_FORWARDED_HOST='evil.example',HTTP_X_FORWARDED_PROTO='https')
        TrustedProxyMiddleware(lambda r:r)(request)
        self.assertNotIn('HTTP_X_FORWARDED_PROTO',request.META);self.assertNotIn('HTTP_X_FORWARDED_HOST',request.META)
        self.assertEqual(request.META['REMOTE_ADDR'],'192.168.221.100')

    def test_profile_beats_stale_windows_auth_and_secure_cookie_environment(self):
        profile=da_dev_profile()
        with patch('portal.deployment.site_settings',return_value=profile),patch.dict(os.environ,{'PORTAL_AD_AUTH_METHOD':'windows','PORTAL_AD_ALLOW_HTTP':'true','DJANGO_SECURE_COOKIES':'false'}):
            spec=importlib.util.spec_from_file_location('portal._profile_test',Path(__file__).resolve().parents[1]/'portal/settings.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertTrue(module.SESSION_COOKIE_SECURE);self.assertTrue(module.CSRF_COOKIE_SECURE)
        self.assertTrue(module.AD_REQUIRE_HTTPS);self.assertTrue(module.AD_SHORT_NAMES)
        self.assertEqual(module.AD_AUTH_METHOD,'ldaps');self.assertEqual(module.AD_SEARCH_BASE,'DC=thermotrade,DC=local')
        self.assertEqual(module.SECURE_PROXY_SSL_HEADER,('HTTP_X_FORWARDED_PROTO','https'))
        self.assertEqual(module.SESSION_COOKIE_NAME,'da_portal_sessionid');self.assertEqual(module.CSRF_COOKIE_NAME,'da_portal_csrftoken')
        self.assertEqual(module.CSRF_TRUSTED_ORIGINS,['https://da-dev.daichi.ru'])
        self.assertNotIn('etl-dev-portal.daichi.ru',module.ALLOWED_HOSTS)

    def test_configuration_tls_failure_leaves_profile_and_monitor_unchanged(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);(root/'.local').mkdir()
            profile=root/'.local/site-settings.json';profile.write_text('{"original":"yes"}')
            monitor=root/'runtime.json';monitor.write_text('{"origins":["http://old"]}')
            with override_settings(BASE_DIR=root,SPLIT_MONITORING_CONFIG=monitor),patch('base.management.commands.configure_da_dev.find_spec',return_value=True),patch('base.management.commands.configure_da_dev.check_tls',side_effect=ssl.SSLCertVerificationError()):
                with self.assertRaises(CommandError):call_command('configure_da_dev',stdout=StringIO())
            self.assertEqual(profile.read_text(),'{"original":"yes"}');self.assertEqual(monitor.read_text(),'{"origins":["http://old"]}')

    def test_configuration_preserves_db_and_monitor_data_and_adds_origin(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);(root/'.local').mkdir()
            (root/'.local/config.json').write_text('{"database_path":"operational.db"}')
            monitor=root/'monitor/runtime.json';monitor.parent.mkdir()
            config={'port':8767,'app_dir':'original-code','data_dir':'original-data','package_sha256':'hash','control_token':'secret','origins':['http://192.168.221.178:8000']}
            monitor.write_text(json.dumps(config));output=StringIO()
            with override_settings(BASE_DIR=root,SPLIT_MONITORING_CONFIG=monitor,AD_CA_CERT_FILE=''),patch('base.management.commands.configure_da_dev.find_spec',return_value=True),patch('base.management.commands.configure_da_dev.check_tls',return_value='TLSv1.3') as tls,patch('base.management.commands.configure_da_dev.stop') as stop:
                call_command('configure_da_dev',stdout=output)
            tls.assert_called_once_with('ldaps://dc-2tt.thermotrade.local:636','',5);stop.assert_called_once_with(monitor)
            configured=json.loads(monitor.read_text());self.assertEqual(configured['origins'],[config['origins'][0],'https://da-dev.daichi.ru'])
            self.assertEqual(configured['data_dir'],config['data_dir']);self.assertEqual(configured['control_token'],config['control_token'])
            self.assertEqual((root/'.local/config.json').read_text(),'{"database_path":"operational.db"}')
            self.assertEqual(site_settings(root)['PORTAL_AD_NETBIOS_DOMAIN'],'TT')
            self.assertNotIn('secret',output.getvalue())

    def test_password_free_preflight_requires_certificate_and_hostname(self):
        from base.ldaps import check_tls
        with patch('base.ldaps.ssl.create_default_context') as context,patch('base.ldaps.socket.create_connection') as tcp:
            context.return_value.wrap_socket.return_value.__enter__.return_value.version.return_value='TLSv1.3'
            self.assertEqual(check_tls('ldaps://dc-2tt.thermotrade.local:636'),'TLSv1.3')
        context.assert_called_once_with(cafile=None)
        context.return_value.wrap_socket.assert_called_once()
        self.assertEqual(context.return_value.wrap_socket.call_args.kwargs['server_hostname'],'dc-2tt.thermotrade.local')
        tcp.assert_called_once_with(('dc-2tt.thermotrade.local',636),timeout=5)

    def test_https_only_applies_without_ldap_dependency_or_network_and_preserves_ad(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);(root/'.local').mkdir()
            original={'PORTAL_AD_AUTH':'true','PORTAL_AD_AUTH_METHOD':'windows','PORTAL_AD_NETBIOS_DOMAIN':'EXISTING','DJANGO_ALLOWED_HOSTS':'localhost'}
            (root/'.local/site-settings.json').write_text(json.dumps(original))
            with override_settings(BASE_DIR=root,SPLIT_MONITORING_CONFIG=root/'not-installed/runtime.json'),patch('base.management.commands.configure_da_dev.find_spec') as dependency,patch('base.management.commands.configure_da_dev.check_tls',side_effect=ConnectionResetError()) as tls:
                call_command('configure_da_dev',https_only=True,stdout=StringIO())
            dependency.assert_not_called();tls.assert_not_called()
            profile=site_settings(root)
            for key in original:
                if key.startswith('PORTAL_AD_'):self.assertEqual(profile[key],original[key])
            self.assertIn('da-dev.daichi.ru',profile['DJANGO_ALLOWED_HOSTS'])
            self.assertEqual(profile['PORTAL_HTTPS_PROXY'],'true');self.assertEqual(profile['DJANGO_SECURE_COOKIES'],'true')
            self.assertEqual(json.loads((root/'.local/site-settings.json.bak').read_text()),original)

    def test_https_only_does_not_enable_ad_when_there_was_no_profile(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            with override_settings(BASE_DIR=root,SPLIT_MONITORING_CONFIG=root/'not-installed/runtime.json'),patch('base.management.commands.configure_da_dev.check_tls') as tls:
                call_command('configure_da_dev',https_only=True,stdout=StringIO())
            self.assertFalse(any(key.startswith('PORTAL_AD_') for key in site_settings(root)));tls.assert_not_called()
