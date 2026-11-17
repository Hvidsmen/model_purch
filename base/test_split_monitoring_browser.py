"""Optional end-to-end check against a supplied private deployment package."""
import importlib.util
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from scripts.split_monitoring_update import write
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from scripts.split_monitoring_runtime import install,stop


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and os.environ.get('SPLIT_MONITORING_TEST_PACKAGE') and importlib.util.find_spec('playwright'),'Requires local private package and Chromium')
class SplitMonitoringBrowserTests(StaticLiveServerTestCase):
    def test_real_package_opens_from_fifth_link_and_api_stays_private(self):
        from playwright.sync_api import sync_playwright,expect
        with TemporaryDirectory() as directory:
            config=Path(directory)/'runtime.json'
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            installed=install(os.environ['SPLIT_MONITORING_TEST_PACKAGE'],[self.live_server_url],config_path=config,port=port)
            user=User.objects.create_superuser('split-browser-admin')
            self.client.force_login(user)
            with override_settings(SPLIT_MONITORING_CONFIG=config),sync_playwright() as driver:
                browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
                context=browser.new_context(viewport={'width':1440,'height':1000})
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();errors=[];page.on('pageerror',lambda exc:errors.append(str(exc)))
                try:
                    page.goto(self.live_server_url+reverse('index_base'))
                    links=page.locator('.portal-projects a');expect(links).to_have_count(5)
                    with page.expect_popup() as popup:
                        page.locator('.portal-projects').get_by_role('link',name='Мониторинг сплитов').click()
                    app=popup.value;app.on('pageerror',lambda exc:errors.append(str(exc)))
                    app.wait_for_url('**/split-monitoring/')
                    expect(app.locator('.lc-page')).to_be_visible(timeout=45000)
                    expect(app.get_by_role('button',name='Обновить из базы')).to_be_visible()
                    expect(app.get_by_role('button',name='Обновить из GitHub')).to_be_visible()
                    expect(app.locator('.lc-matrix').first).to_be_visible(timeout=45000)
                    self.assertEqual(app.request.get(self.live_server_url+'/card-api/meta').status,200)
                    anon=browser.new_context()
                    self.assertEqual(anon.request.get(self.live_server_url+'/card-api/meta').status,401)
                    self.assertEqual(anon.request.get(self.live_server_url+'/split-monitoring/snapshot.'+'a'*64+'.json',max_redirects=0).status,302)
                    anon.close()
                    app.locator('.lc-source-switch button').last.click()
                    expect(app.locator('.lc-matrix').first).to_be_visible()
                    expect(app.locator('.lc-source-switch button').last).to_have_class('active')
                    expect(app.locator('.lc-selection')).to_contain_text('Архив Excel',timeout=45000)
                    expect(app.locator('.lc-workspace')).to_have_attribute('aria-busy','false')
                    Path('/workspace/output').mkdir(exist_ok=True)
                    app.screenshot(path='/workspace/output/split-monitoring-integrated.png',full_page=True)
                    self.assertEqual(errors,[])
                    job={'id':'e'*32,'actor':user.pk,'state':'completed','stage':'Готово','message':'Мониторинг обновлён.',
                         'started_at':'2026-11-17T10:00:00+00:00','finished_at':'2026-11-17T10:00:04+00:00','steps':[],'target_commit':'f'*40}
                    write(config.parent/'updates'/job['id']/'status.json',job)
                    with patch('base.split_monitoring_update_views.start',return_value=job) as update:
                        app.get_by_role('button',name='Обновить из GitHub').click()
                        expect(app.get_by_role('heading',name='Обновление мониторинга сплитов')).to_be_visible()
                        expect(app.locator('#monitor-update-message')).to_have_text('Мониторинг обновлён.')
                        expect(app.locator('#monitor-update-state')).to_have_text('Завершено')
                        update.assert_called_once_with(config,user.pk)
                    app.screenshot(path='/workspace/output/split-monitoring-updates.png',full_page=True)
                    self.assertEqual(errors,[])
                finally:
                    context.close();browser.close();stop(config)
