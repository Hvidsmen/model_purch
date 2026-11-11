import importlib.util
import os
import json
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_versions_browser as fixtures
from .models import MotivationCalculationSchedule


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Optional Chromium check')
class CalculationLiveBrowserTests(StaticLiveServerTestCase):
    setUp=fixtures.GlobalVersionBrowserTests.setUp
    db=fixtures.GlobalVersionBrowserTests.db

    def test_running_card_shows_stage_timer_and_light_poll(self):
        from playwright.sync_api import expect
        job=self.db(lambda:MotivationCalculationSchedule.objects.create(name='Daily progress',fact_year=2026,plan_version='Plan',synced=True))
        monitor=reverse('motivation_calculation_monitor',args=[job.pk]);requests=[]
        def respond(route):
            requests.append(route.request.url)
            route.fulfill(status=200,content_type='application/json',body=json.dumps({'status':'Выполняется','history':[],
                'checked_at':'14:22:54','progress':{'started_at':'2026-11-11T14:20:49+03:00','elapsed':125,
                'stage':'Раздельный сценарий · Объединение плана и факта, базы выполнения',
                'waiting':'Ожидает снятия блокировки: сеанс 81'}}))
        self.page.route('**'+monitor+'*',respond)
        self.page.route('**'+reverse('motivation_prepared_sales_monitor')+'*',lambda route:route.fulfill(status=503,content_type='application/json',body='{}'))
        self.page.goto(self.live_server_url+reverse('motivation_calculations'))
        card=self.page.locator(f'#job-{job.pk}')
        expect(card.locator('[data-job-progress]')).to_be_visible()
        expect(card.locator('[data-job-stage]')).to_contain_text('Раздельный сценарий')
        expect(card.locator('[data-job-waiting]')).to_contain_text('сеанс 81')
        expect(card.locator('[data-job-elapsed]')).to_contain_text('2 мин')
        expect(card.locator('[data-job-last]')).to_contain_text('Выполняется')
        expect(card.get_by_role('button',name='Запустить сейчас',exact=True)).to_be_disabled()
        self.assertTrue(requests and all('light=1' in url for url in requests))
        self.assertEqual(self.errors,[])
