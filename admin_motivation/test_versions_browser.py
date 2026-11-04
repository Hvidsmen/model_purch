Warning: truncated output (original token count: 44449)
Total output lines: 2282

from base.testing import authorize_test_case
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from .models import GlobalCoeffVersion, GlobalCoeff, Goods, SegmentCoeff, TypeCoeff, VariationCalculate, Subdivision, SubdivisionCoeff, SubdivisionManagerCoeff, Chanel, KindManagerCoeff


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class GlobalVersionBrowserTests(StaticLiveServerTestCase):
    def test_new_reports_compact_role_screens(self):
        from contextlib import nullcontext
        from uuid import uuid4
        from django.utils import timezone
        from django.contrib.auth.models import User, Group
        from base.models import MotivationReportAccess
        from base.access import POLITICIAN
        from .models import SalesPlanScenario, MotivationExecution
        from .services.manager_detail import manager_detail_report, period_motivation_summary
        from .test_manager_detail import detail_rows
        from playwright.sync_api import expect
        def prepare():
            channel = Chanel.objects.create(chanel_name='Report design')
            branch = Subdivision.objects.create(subdivision_key='A', subdivision_name='A', subdivision_global='A', chanel=channel)
            employee = User.objects.create_user('design-employee')
            employee.groups.add(Group.objects.get(name=POLITICIAN))
            MotivationReportAccess.objects.create(user=employee, subdivision=branch, deal_author='Иванов')
            SalesPlanScenario.objects.create(title='План 2026', source_version='Visual plan', loaded_at=timezone.now())
            MotivationExecution.objects.create(run_id=uuid4(), job_key=uuid4(), state='completed', started_at=timezone.now(), completed_at=timezone.now(), parameters={'fact_year': 2026, 'plan_version': 'Visual plan', 'quarter_share': '80', 'year_share': '20', 'payout_threshold': '.8'})
            return employee.pk
        employee = self.db(prepare)
        source = [dict(row, АвторСделки=name, КварталМотивации='4 Квартал(1-4 кв)' if row['КварталМотивации'] != 'Год' else 'Год') for name in ('Иванов', 'Петров', 'Сидоров') for row in detail_rows()]
        def load(kind, values, **kwargs):
            rows = [row for row in source if not values.get('DealAuthor') or row['АвторСделки'] in values['DealAuthor']]
            if kwargs.get('summary_only'):
                return {'currency': 'USD', 'period_summary': period_motivation_summary(rows)}
            return manager_detail_report(rows, performance_rows=[dict(row, КварталМотивации='4 Квартал(1-4 кв)' if row['КварталМотивации'] != 'Год' else 'Год') for row in detail_rows()])
        from .services.plan_summary import row as budget_row
        budget = [{**budget_row(label, 200000, 24000, 27000, ddp=100000,
                   children=[budget_row(name, 200000, global_value, local_value, ddp=100000)
                             for name, global_value, local_value in [('Политика', 12000, 15000), ('Продажи', 8000, 8000), ('Директор', 4000, 4000)]]),
                   'difference_percent': .125} for label in ('Компания', 'Д-Москва')]
        with patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.services.reports_next.load_report', side_effect=load), patch('admin_motivation.services.reports_next.planned_budget', return_value=budget):
            self.page.set_viewport_size({'width':1440,'height':1000})
            self.page.goto(self.live_server_url + reverse('index_admin_motivation'))
            expect(self.page.get_by_role('heading', name='Мотивация', exact=True, level=1)).to_be_visible()
            expect(self.page.locator('.ux-home-primary')).to_contain_text('Работа с моделью')
            self.page.screenshot(path='/tmp/motivation-home-ux.png', full_page=True)
            self.page.goto(self.live_server_url + reverse('motivation_reports_next'))
            expect(self.page.get_by_role('link', name='Моя премия', exact=False)).to_be_visible()
            self.page.screenshot(path='/tmp/new-reports-catalog-compact.png', full_page=True)
            self.page.goto(self.live_server_url + reverse('motivation_report_next', args=['mine']) + f'?run=1&preview_user={employee}&type=Политики')
            expect(self.page.locator('.nr-period:not(.nr-goal):not(.nr-note):not(.nr-summary-card)')).to_have_count(2)
            expect(self.page.locator('.next-total')).to_contain_text('180,00')
            expect(self.page.get_by_role('heading', name='Сколько осталось до 100%')).to_be_visible()
            expect(self.page.get_by_role('heading', name='Из каких товаров сложилась премия')).to_be_visible()
            expect(self.page.locator('.nr-target-reward').first).to_contain_text('Пока нельзя рассчитать')
            expect(self.page.locator('.nr-matrix-node').first).to_be_visible()
            expect(self.page.locator('.nr-matrix-header')).to_contain_text('Возможная премия')
            expect(self.page.locator('.nr-matrix-total')).to_be_visible()
            expect(self.page.locator('.nr-with-total').first.locator(':scope > article')).to_have_count(3)
            expect(self.page.locator('.nr-goal.nr-summary-card')).to_have_count(1)
            expect(self.page.locator('.nr-with-total').first.locator(':scope > article').nth(2)).to_contain_text('Итого за выбранные периоды')
            self.assertGreater(len(self.page.locator('.nr-matrix-header').evaluate('(el) => getComputedStyle(el).gridTemplateColumns').split()), 8)
            self.assertGreaterEqual(float(self.page.locator('.nr-matrix-header > span').first.evaluate('(el) => getComputedStyle(el).fontSize').replace('px','')), 13)
            expect(self.page.locator('.motivation-workflow')).to_have_count(0)
            expect(self.page.locator('.nr-kpis')).to_have_count(0)
            self.page.locator('[data-matrix-details]').uncheck()
            expect(self.page.locator('.nr-matrix')).to_have_attribute('data-mode', 'basic')
            expect(self.page.locator('.nr-matrix-header .nr-matrix-execution').first).to_be_hidden()
            self.page.locator('[data-matrix-details]').check()
            expect(self.page.locator('.nr-matrix-header .nr-matrix-execution').first).to_be_visible()

            self.page.screenshot(path='/tmp/new-reports-mine-compact.png', full_page=True)
            self.page.locator('.nr-matrix-node > summary').first.click()
            self.page.locator('.nr-matrix-children > .nr-matrix-node > summary').first.click()
            self.page.get_by_role('link', name='Ставки и отгрузки', exact=True).first.click()
            expect(self.page.locator('#next-deep-calculation')).to_have_attribute('open', '')
            expect(self.page.locator('#manager-detail-extra')).to_be_checked()
            expect(self.page.locator('.manager-detail-cards')).to_have_count(0)
            self.page.goto(self.live_server_url + reverse('motivation_report_next', args=['detail']) + '?run=1&type=Политики')
            expect(self.page.locator('#next-deep-calculation')).to_have_attribute('open', '')
            expect(self.page.locator('#manager-detail-panel')).to_be_visible()
            expect(self.page.locator('#manager-detail-extra')).not_to_be_checked()
            self.assertTrue(self.page.locator('#manager-detail-scroll').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1'))
            expect(self.page.locator('#manager-detail-scroll-top')).to_be_hidden()
            self.page.screenshot(path='/tmp/detail-report-fit-desktop.png', full_page=True)
            self.page.locator('#manager-detail-extra').check()
            expect(self.page.locator('#manager-detail-report')).to_have_class('manager-expanded-columns')
            expect(self.page.locator('#manager-detail-scroll-top')).to_be_visible()
            self.page.locator('#manager-detail-extra').uncheck()
            self.assertTrue(self.page.locator('#manager-detail-scroll').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1'))
            self.page.goto(self.live_server_url + reverse('motivation_report_next', args=['mine']) + f'?run=1&preview_user={employee}&type=Политики')
            # Personal cards stay compact with amounts visible and explanations folded.
            period_cards = self.page.locator('.nr-personal-cards').first.locator(':scope > article')
            self.assertLess(period_cards.first.bounding_box()['height'], 210)
            self.assertLess(self.page.locator('.nr-personal-cards .nr-goal').first.bounding_box()['height'], 450)
            expect(self.page.locator('.nr-goal-explanation').first).not_to_have_attribute('open', '')
            self.page.locator('.nr-goal-explanation > summary').first.click()
            expect(self.page.locator('.nr-target-reward').first).to_be_visible()
            self.page.locator('.nr-goal-explanation > summary').first.click()
            for row in source:
                row['VariationCalculate'] = 'Подразделение(Глобально)'
            self.page.reload()
            formula = self.page.locator('.nr-matrix-formula').first
            expect(formula.locator(':scope > summary')).to_contain_text('Подразделение(Глобально)')
            children = formula.locator(':scope > .nr-matrix-children')
            expect(children).to_be_hidden()
            expect(self.page.locator('.nr-matrix-total').first).to_be_visible()
            formula.locator(':scope > summary').click()
            expect(children).to_be_visible()
            child = children.locator('.nr-matrix-node').first
            child.locator(':scope > summary').click()
            expect(child.locator(':scope > .nr-matrix-children')).to_be_visible()
            formula.locator(':scope > summary').click()
            expect(children).to_be_hidden()
            expect(child.locator(':scope > .nr-matrix-children')).to_be_hidden()
            expect(self.page.locator('.nr-matrix-division').first).to_be_visible()
            formula.locator(':scope > summary').focus()
            self.page.keyboard.press('Enter')
            expect(children).to_be_visible()
            self.page.locator('[data-matrix-details]').uncheck()
            expect(formula.locator(':scope > summary .nr-matrix-execution').first).to_be_hidden()
            expect(formula.locator(':scope > summary .nr-matrix-calculated').first).to_be_visible()
            self.page.locator('[data-matrix-details]').check()
            formula.locator(':scope > summary').click()
            self.page.screenshot(path='/tmp/my-premium-compact-global-desktop.png', full_page=True)
            self.page.set_viewport_size({'width':390,'height':844})
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
            self.page.screenshot(path='/tmp/my-premium-compact-global-mobile.png', full_page=True)
            self.page.set_viewport_size({'width':1440,'height':1000})

            self.page.goto(self.live_server_url + reverse('motivation_report_next', args=['team']) + '?run=1')
            expect(self.page.locator('.nr-table')).to_have_count(3)
            expect(self.page.locator('.nr-kpi-types')).to_have_count(3)
            expect(self.page.locator('.nr-kpi').filter(has_text='Премия без учёта выполнения плана')).to_contain_text('1 800,00')
            expect(self.page.locator('select[name=execution]')).to_have_count(0)
            for label in ('Политики', 'Продажи', 'Директора'):
                expect(self.page.get_by_role('heading', name=label, exact=True)).to_be_visible()
            self.page.screenshot(path='/tmp/new-reports-team-types-desktop.png', full_page=True)
            self.page.set_viewport_size({'width':390,'height':844})
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
            self.page.screenshot(path='/tmp/new-reports-team-types-mobile.png', full_page=True)
            self.page.set_viewport_size({'width':1440,'height':1000})
            self.page.goto(self.live_server_url + reverse('motivation_report_next', args=['team']) + '?run=1&type=Политики')
            expect(self.page.locator('.nr-table tbody tr')).to_have_count(3)
            expect(self.page.get_by_role('heading', name='Итого по видам мотивации')).to_be_visible()
            expect(self.page.locator('.nr-table thead')).to_contain_text('4 кв.')
            expect(self.page.get_by_text('По периодам', exact=True)).to_have_count(0)
            search = self.page.get_by_role('searchbox', name='Найти сотрудника во всём отчёте')
            search.fill('Петров')
            with self.page.expect_navigation():
                self.page.get_by_role('button', name='Найти', exact=True).click()
            expect(self.page.locator('.nr-table tbody tr')).to_have_count(1)
            expect(self.page.locator('.nr-table tbody')).to_contain_text('Петров')
            self.page.screenshot(path='/tmp/new-reports-team-compact.png', full_page=True)
            self.page.goto(self.live_server_url + reverse('motivation_report_next', args=['registry']) + '?run=1&type=Политики')
            expect(self.page.locator('.nr-table tbody tr')).to_have_count(3)
            expect(self.page.locator('.nr-table thead')).to_contain_text('Год')
            self.page.screenshot(path='/tmp/new-reports-registry-pivot.png', full_page=True)
            self.page.goto(self.live_server_url + reverse('motivation_report_next', args=['registry']) + '?run=1')
            expect(self.page.locator('.nr-table')).to_have_count(3)
            expect(self.page.locator('.nr-kpi-types')).to_have_count(3)
            self.page.screenshot(path='/tmp/registry-types-desktop.png', full_page=True)

            self.page.goto(self.live_server_url + reverse('motivation_report_next', args=['executive']) + '?run=1')
            expect(self.page.locator('.nr-exec-scroll')).to_be_visible()
            expect(self.page.locator('.nr-exec-header')).to_contain_text('DDP, USD')
            expect(self.page.locator('.nr-kpi-types')).to_have_count(3)
            self.page.locator('[data-executive-expand]').click()
            expect(self.page.locator('.nr-exec-children').first).to_be_visible()
            self.page.locator('[data-executive-collapse]').click()
            expect(self.page.locator('.nr-exec-children').first).to_be_hidden()
            self.page.locator('.nr-filter-panel > summary').click()
            self.page.locator('.next-types > summary').click()
            self.page.locator('[data-type-all]').click()
            expect(self.page.locator('[data-report-pending]')).to_be_visible()
            expect(self.page.locator('.nr-filter-panel')).to_have_attribute('data-dirty', '')
            self.page.screenshot(path='/tmp/new-reports-pending-filters.png', full_page=True)
            self.page.locator('.next-types > summary').press('Escape')
            self.page.locator('[data-report-revert]').click()
            expect(self.page.locator('[data-report-pending]')).to_be_hidden()
            expect(self.page.locator('.next-types input:checked')).to_have_count(0)
            self.page.locator('.next-types > summary').click()
            self.page.locator('[data-type-all]').click()
            expect(self.page.locator('.next-types input:checked')).to_have_count(3)
            self.page.locator('[data-type-clear]').click()
            expect(self.page.locator('.next-types input:checked')).to_have_count(0)
            self.page.locator('input[name=type][value="Политики"]').check()
            self.page.locator('input[name=type][value="Продажи"]').check()
            expect(self.page.locator('[data-type-caption]')).to_have_text('Политики, Продажи')
            self.page.locator('.next-periods > summary').click()
            self.page.locator('input[name=period][value="4"]').check()
            self.page.locator('input[name=period][value="5"]').check()
            self.page.locator('.next-periods > summary').click()
            with self.page.expect_navigation():
                self.page.get_by_role('button', name='Показать', exact=True).click()
            expect(self.page.locator('.next-types input:checked')).to_have_count(2)
            expect(self.page.locator('.next-periods input:checked')).to_have_count(2)
            expect(self.page.locator('[data-report-pending]')).to_be_hidden()

            self.page.screenshot(path='/tmp/new-reports-executive-compact.png', full_page=True)
            self.page.set_viewport_size({'width':390,'height':844})
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
            self.page.screenshot(path='/tmp/new-reports-mobile-compact.png', full_page=True)
        self.assertEqual(self.errors, [])

    def test_new_report_tab_plan_choice_and_employee_drilldown(self):
        from contextlib import nullcontext
        from uuid import uuid4
        from django.utils import timezone
        from .models import SalesPlanScenario, MotivationExecution
        from .services.manager_detail import manager_detail_report
        from .test_manager_detail import detail_rows
        from playwright.sync_api import expect
        def prepare():
            plans = [SalesPlanScenario.objects.create(title=title, source_version=title, loaded_at=timezone.now()) for title in ('Run plan', 'Alternative plan')]
            execution = MotivationExecution.objects.create(run_id=uuid4(), job_key=uuid4(), state='completed', started_at=timezone.now(), completed_at=timezone.now(), parameters={'fact_year': 2026, 'plan_version': 'Run plan', 'quarter_share': '80', 'year_share': '20'})
            return plans[1].pk
        alternate = self.db(prepare)
        budget = [{'name': 'Компания', 'global': 100, 'local': 120, 'difference': 20, 'children': []}]
        annual = manager_detail_report([detail_rows()[1]])
        with patch('admin_motivation.services.reports_next.planned_budget', return_value=budget) as comparison, patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.services.reports_next.load_report', return_value=annual):
            self.page.goto(self.live_server_url + reverse('motivation_reports'))
            self.page.get_by_role('link', name='Новые отчёты · пилот', exact=True).click()
            expect(self.page.get_by_role('heading', name='Новые отчёты мотивации')).to_be_visible()
            self.page.get_by_role('link', name='Сводный отчёт: план премий', exact=False).click()
            self.page.select_option('select[name=plan]', str(alternate))
            self.page.locator('.next-periods > summary').click()
            self.page.locator('[data-period-all]').click()
            expect(self.page.locator('.next-periods input:checked')).to_have_count(2)
            self.page.locator('[data-period-clear]').click()
            expect(self.page.locator('.next-periods input:checked')).to_have_count(0)
            self.page.locator('input[name=period][value="4"]').check()
            self.page.locator('input[name=period][value="5"]').check()
            self.page.locator('.next-periods > summary').click()

            self.page.get_by_role('button', name='Показать', exact=True).click()
            expect(self.page.locator('.nr-kpi').nth(2)).to_contain_text('20,00')
            self.assertEqual(comparison.call_args.args[1].pk, alternate)
            self.assertEqual(comparison.call_args.args[2].getlist('period'), ['4', '5'])
            self.page.goto(self.live_server_url + reverse('motivation_report_next', args=['team']) + '?run=1&type=Политики&period=5')
            expect(self.page.locator('.next-total')).to_contain_text('90,00 USD')
            self.page.get_by_role('link', name='Как рассчитано →', exact=True).first.click()
            expect(self.page.get_by_role('heading', name='Как получилась сумма')).to_be_visible()
            expect(self.page.locator('#next-deep-calculation')).to_have_attribute('open', '')
            expect(self.page.locator('#manager-detail-panel')).to_be_visible()
            expect(self.page.locator('.manager-detail-cards')).to_have_count(0)
            expect(self.page.locator('#manager-detail-nodes')).to_contain_text('Иванов')
            self.page.screenshot(path='/tmp/new-motivation-reports-detail.png', full_page=True)
        self.assertEqual(self.errors, [])

    def test_coverage_bulk_button_includes_other_pages(self):
        from playwright.sync_api import expect
        from .models import SalesPlanScenario, SalesPlanLine
        from django.utils import timezone
        def prepare():
            scenario = SalesPlanScenario.objects.create(title='Bulk coverage', loaded_at=timezone.now())
            SalesPlanLine.objects.bulk_create([SalesPlanLine(scenario=scenario, plan_date=date(2026, 1, 1),
                subdivision='A', planning_group_sales='Bulk planning', group='Bulk ERP',
                brand=f'Bulk browser {i}', amount_usd=100) for i in range(105)])
            return scenario.pk
        self.db(lambda: SegmentCoeff.objects.filter(pk=self.coeff.segment_id).update(segment_name='K0'))
        plan = self.db(prepare)
        self.page.goto(self.live_server_url + reverse('motivation_coefficient_coverage') + f'?version={self.first.pk}&plan={plan}&scope=global&kind=Политики&run=1')
        self.page.get_by_role('button', name='Создать для всех найденных пропусков', exact=True).click()
        expect(self.page.get_by_role('heading', name='Создать коэффициенты для всех пропусков')).to_be_visible()
        expect(self.page.locator('tbody tr')).to_have_count(105)
        self.page.get_by_role('button', name='Создать все предложенные коэффициенты в черновике', exact=True).click()
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.filter(version=self.first, goods__brand__startswith='Bulk browser').count()), 525)
        self.assertEqual(self.errors, [])

    def test_coverage_scope_and_create_missing_cells(self):
        from playwright.sync_api import expect
        from .models import SalesPlanScenario, SalesPlanLine
        from django.utils import timezone
        def prepare():
            scenario = SalesPlanScenario.objects.create(title='Coverage repair', loaded_at=timezone.now())
            SalesPlanLine.objects.create(scenario=scenario, plan_date=date(2026, 1, 1),
                subdivision='A', planning_group_sales='Repair planning', group='Repair ERP',
                brand='Repair brand', amount_usd=100)
            return scenario.pk
        plan = self.db(prepare)
        self.page.goto(self.live_server_url + reverse('motivation_coefficient_coverage') + f'?version={self.first.pk}&plan={plan}&scope=global&kind=Политики&run=1')
        expect(self.page.locator('select[name=scope]')).to_have_value('global')
        self.page.locator('.coverage-issue-group > summary').first.click()
        self.page.get_by_role('link', name='Создать коэффициенты', exact=True).click()
        expect(self.page.get_by_role('heading', name='Создать недостающие коэффициенты')).to_be_visible()
        for i in range(5):
            self.page.locator(f'input[name=K{i}]').fill('0.5')
        self.page.screenshot(path='/tmp/coverage-create-coefficients.png', full_page=True)
        self.page.get_by_role('button', name='Создать в черновике', exact=True).click()
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.filter(version=self.first, goods__brand='Repair brand').count()), 5)
        self.assertEqual(self.errors, [])

    def test_discount_segment_form_copy_history_and_nulls(self):
        from playwright.sync_api import expect
        from .models import DiscountSegment
        from .services.approval import approve_review, review_approval
        from .services.versions import create_version
        self.page.goto(self.live_server_url + reverse('motivation_discount_segments'))
        expect(self.page.get_by_role('heading', name='Сегменты скидок', exact=True)).to_be_visible()
        self.page.fill('#id_segments-0-type_discount', 'MASS')
        for index, value in enumerate(('', '10', '17', '19', '24')):
            self.page.fill(f'#id_segments-0-o{index}', value)
        self.page.get_by_role('button', name='Сохранить сегменты', exact=True).click()
        expect(self.page.locator('.discount-notice')).to_contain_text('сохранены')
        self.assertIsNone(self.db(lambda: DiscountSegment.objects.get(version=self.first).o0))
        self.db(lambda: approve_review(review_approval(GlobalCoeffVersion.objects.get(pk=self.first.pk))['token']))
        latest = self.db(lambda: create_version(date(2026, 1, 1), source_version=self.first.pk).pk)
        self.page.goto(self.live_server_url + reverse('motivation_discount_segments') + f'?version={latest}')
        expect(self.page.locator('#id_segments-0-o1')).to_have_value('10.0000')
        self.page.fill('#id_segments-0-o4', '30')
        self.page.fill('#id_segments-1-type_discount', 'NO_DISCOUNT')
        self.page.get_by_role('button', name='Сохранить сегменты', exact=True).click()
        expect(self.page.locator('.discount-notice')).to_contain_text('сохранены')
        self.assertIsNone(self.db(lambda: DiscountSegment.objects.get(version_id=latest, type_discount='NO_DISCOUNT').o4))
        self.page.set_viewport_size({'width':1440,'height':1000})
        self.page.screenshot(path='/tmp/da-discount-segments.png',full_page=True)
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
        self.page.goto(self.live_server_url + reverse('motivation_discount_segments') + f'?version={self.first.pk}')
        expect(self.page.locator('#id_segments-0-o4')).to_be_disabled()
        expect(self.page.locator('#id_segments-0-o4')).to_have_value('24.0000')
        self.assertEqual(self.errors, [])

    def test_pilot_periods_for_administrator_only_offer_q4_and_annual(self):
        from playwright.sync_api import expect
        self.page.goto(self.live_server_url+reverse('motivation_report_next',args=['team']))
        self.assertEqual(self.page.locator('input[name=period]').evaluate_all('(items)=>items.map(item=>item.value)'),['4','5'])
        self.page.locator('.next-periods > summary').click()
        expect(self.page.locator('input[name=period][value="4"]')).to_be_visible()
        expect(self.page.locator('input[name=period][value="5"]')).to_be_visible()
        self.page.locator('input[name=period][value="4"]').check()
        self.page.locator('input[name=period][value="5"]').check()
        self.assertEqual(self.page.locator('input[name=period]:checked').count(),2)
        self.page.screenshot(path='/tmp/pilot-q4-year-periods.png',full_page=True)
        self.page.goto(self.live_server_url+reverse('motivation_calculations'))
        expect(self.page.locator('.admin-notice')).to_contain_text('нарастающим итогом')
        self.assertEqual(self.errors,[])

    def test_spreadsheet_preview_undo_validation_and_preflight(self):
        from playwright.sync_api import expect
        self.page.goto(self.live_server_url + reverse('global_coeff_admin_motivation'))
        field=self.page.locator(f'[name="global_coeff={self.coeff.pk}"]')
        original=field.input_value()
        field.click()
        self.page.locator('#editor-paste-open').click()
        self.page.locator('#editor-sheet-data').fill('0,25%')
        self.page.locator('#editor-sheet-check').click()
        expect(self.page.locator('#editor-sheet-preview')).to_contain_text('0.25%')
        self.page.locator('#editor-sheet-apply').click()
        expect(self.page.locator('#editor-dirty-count')).to_have_text('Изменено: 1')
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.get(pk=self.coeff.pk).motivation_coeff),.123456)
        self.page.locator('#editor-undo').click()
        expect(field).to_have_value(original)
        field.click();self.page.locator('#editor-paste-open').click()
        self.page.locator('#editor-sheet-data').fill('200%')
        self.page.locator('#editor-sheet-check').click()
        expect(self.page.locator('#editor-sheet-apply')).to_be_disabled()
        expect(self.page.locator('#editor-sheet-error')).to_contain_text('диапазон')
        self.page.locator('#editor-sheet-cancel').click()
        self.page.locator('#editor-preflight').click()
        expect(self.page.locator('#editor-preflight-status')).to_contain_text('Правки корректны',timeout=30000)
        self.page.locator('#editor-preflight-close').click()
        self.assertEqual(self.errors,[])

    def test_bulk_formula_reopen_and_unused_director_delete(self):
        from playwright.sync_api import expect
        from .models import DirectorEmployee, VersionReopenLog
        from .services.pilot_formula import GLOBAL_SUBDIVISION
        from .services.approval import review_approval, approve_review
        def setup_second():
            original = GlobalCoeff.objects.get(pk=self.coeff.pk)
            good = Goods.objects.create(goods_key='OtherKey', planning_group_sales='Other', group='Other ERP', brand='Other brand')
            row = GlobalCoeff.objects.create(version=self.first, goods=good, type_coeff=original.type_coeff, segment=original.segment, variation_calculate=original.variation_calculate, motivation_coeff=.2, manager_coeff=1)
            return good.pk, row.pk, VariationCalculate.objects.get_or_create(variation_name=GLOBAL_SUBDIVISION)[0].pk
        good, row, formula = self.db(setup_second)
        self.page.goto(self.live_server_url + reverse('global_coeff_admin_motivation'))
        self.page.get_by_role('button',name='Изменить ставки',exact=True).click()
        self.page.locator('#multiply-operation').select_option('formula')
        self.page.locator('#coefficient-search').fill('OtherKey')
        self.page.locator('#multiply-filtered').click()
        self.page.locator('#multiply-formula').select_option(str(formula))
        self.page.locator('#multiply-preview').click()
        self.page.locator('#multiply-apply').click()
        self.assertNotEqual(self.db(lambda: GlobalCoeff.objects.get(pk=row).variation_calculate_id),formula)
        self.page.locator('#editor-save').click()
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.get(pk=row).variation_calculate_id),formula)
        self.assertNotEqual(self.db(lambda: GlobalCoeff.objects.get(pk=self.coeff.pk).variation_calculate_id),formula)
        self.db(lambda: approve_review(review_approval(GlobalCoeffVersion.objects.get(pk=self.first.pk))['token']))
        self.page.reload()
        self.page.locator('.version-tools button[aria-controls="version-reopen"]').click()
        self.page.fill('#version-reopen input[name=reason]','Browser correction')
        self.page.locator('#version-reopen input[name=confirm]').check()
        self.page.get_by_role('button',name='Вернуть в черновик',exact=True).click()
        expect(self.page.locator('#version-panel')).to_contain_text('Версия возвращена')
        self.assertTrue(self.db(lambda: VersionReopenLog.objects.filter(version=self.first).exists()))
        director = self.db(lambda: DirectorEmployee.objects.create(full_name='Delete browser director').pk)
        self.page.goto(self.live_server_url + reverse('motivation_director_directory'))
        record = self.page.locator('tbody tr').filter(has_text='Delete browser director')
        record.locator('.director-delete>summary').click()
        record.locator('input[name=confirm]').check()
        record.get_by_role('button',name='Подтвердить удаление',exact=True).click()
        expect(self.page.get_by_role('status')).to_contain_text('Директор удалён')
        self.assertFalse(self.db(lambda: DirectorEmployee.objects.filter(pk=director).exists()))
        self.assertEqual(self.errors, [])

    def test_version_tools_compact_layout_and_director_bulk_formula(self):
        from .models import GlobalDirectorCoeff
        from playwright.sync_api import expect
        self.good = self.db(lambda: self.coeff.goods)
        formula = self.db(lambda: VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')[0].pk)
        row = self.db(lambda: GlobalDirectorCoeff.objects.create(version=self.first,goods=self.good,k0='.01',k4='.05').pk)
        self.page.set_viewport_size({'width':1920,'height':1080})
        self.page.goto(self.live_server_url + reverse('global_coeff_admin_motivation'))
        self.page.get_by_role('button',name='Добавить товар',exact=True).click()
        expect(self.page.locator('#coefficient-action-hub #add-product-details')).to_have_attribute('open','')
        self.page.get_by_role('button',name='Импорт и справочники',exact=True).click()
        expect(self.page.locator('#add-product-details')).not_to_have_attribute('open','')
        self.assertEqual(self.page.locator('#coefficient-tools input[type=file]').evaluate('(el) => el.form.id'),'mainCoeffForm')
        self.page.get_by_role('button',name='Импорт и справочники',exact=True).click()
        geometry = self.page.evaluate("""() => {
            const box = selector => document.querySelector(selector).getBoundingClientRect();
            return {tools:box('.version-tools').x,picker:box('.version-picker').right,
                    variants:box('.compare-variants').x,compare:box('#comparison-refresh').right};
        }""")
        self.assertGreater(geometry['tools'],geometry['picker'])
        self.assertGreater(geometry['variants'],geometry['compare'])
        self.page.locator('.version-tools button[aria-controls="version-history"]').click()
        expect(self.page.locator('#version-history table')).to_be_visible()
        self.page.locator('.version-tools button[aria-controls="create-version-details"]').click()
        expect(self.page.locator('#version-history')).not_to_have_attribute('open','')
        panel_box = self.page.locator('#version-panel').bounding_box()
        content_box = self.page.locator('#create-version-details .version-tool-content').bounding_box()
        self.assertGreaterEqual(panel_box['y'] + panel_box['height'], content_box['y'] + content_box['height'])
        self.page.screenshot(path='/tmp/portal-version-create-inline.png',full_page=True)
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#create-version-details')).not_to_have_attribute('open','')
        self.page.get_by_role('button',name='Изменить ставки',exact=True).click()
        self.page.locator('#multiply-operation').select_option('formula')
        self.page.locator('[data-multiply-kind=policies]').uncheck()
        self.page.locator('[data-multiply-kind=directors]').check()
        self.page.locator('#multiply-formula').select_option(str(formula))
        self.page.locator(f'.bulk-good[value="{self.good.pk}"]').check()
        self.page.locator('#multiply-preview').click()
        self.page.locator('#multiply-apply').click()
        with self.page.expect_navigation():
            self.page.locator('#editor-save').click()
        self.assertEqual(self.db(lambda: GlobalDirectorCoeff.objects.get(pk=row).variation_calculate_id),formula)
        self.assertEqual(str(self.db(lambda: GlobalDirectorCoeff.objects.get(pk=row).k0)),'0.010000')
        expect(self.page.get_by_text('Директор: Подразделение(Глобально)',exact=True)).to_be_visible()
        self.page.set_viewport_size({'width':390,'height':844})
        self.page.locator('.version-tools button[aria-controls="version-history"]').click()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),391)
        self.assertLessEqual(self.page.locator('#version-history .version-tool-content').bounding_box()['x'] + self.page.locator('#version-history .version-tool-content').bounding_box()['width'],391)
        self.page.screenshot(path='/tmp/portal-compact-version-tools.png',full_page=True)

    def test_workflow_grouping_and_inline_product_settings_preserve_edits(self):
        from playwright.sync_api import expect
        formula=self.db(lambda: VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')[0].pk)
        self.page.set_viewport_size({'width':1920,'height':1080})
        self.page.goto(self.live_server_url+reverse('global_coeff_admin_motivation'))
        expect(self.page.locator('.workflow-steps')).to_contain_text('Утверждение')
        group=self.page.locator('.coefficient-group-heading button')
        group.click();expect(self.page.locator('.coefficient-row')).not_to_be_visible()
        group.click();expect(self.page.locator('.coefficient-row')).to_be_visible()
        self.page.locator('.brand-settings > summary').click()
        panel=self.page.locator('.coefficient-row .brand-settings')
        expect(self.page.locator('.motivation-side-panel')).to_have_count(0)
        expect(panel).to_be_visible()
        panel.locator('select').select_option(str(formula))
        expect(panel.locator('.formula-explanation')).to_contain_text('личный объём')
        self.page.screenshot(path='/tmp/motivation-ux-desktop.png',full_page=True)
        panel.locator('summary').click()
        expect(self.page.locator('#mainCoeffForm .select-var-calc')).to_have_value(str(formula))
        expect(self.page.locator('#editor-dirty-count')).not_to_have_text('Изменено: 0')
        self.page.set_viewport_size({'width':390,'height':844})
        self.page.locator('.brand-settings > summary').click()
        expect(panel).to_be_visible()
        self.assertLessEqual(panel.bounding_box()['width'],390)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),391)
        self.page.screenshot(path='/tmp/motivation-ux-mobile.png',full_page=True)
        self.page.keyboard.press('Escape')
        expect(panel).not_to_have_attribute('open','')
        self.assertEqual(self.errors,[])

    def test_completed_job_links_to_report_with_recorded_context(self):
        from playwright.sync_api import expect
        from django.utils import timezone
        from uuid import uuid4
        from .models import MotivationCalculationSchedule, MotivationExecution
        def setup_run():
            job=MotivationCalculationSchedule.objects.create(name='Report link',fact_year=2026,plan_version='Recorded plan',synced=True)
            run=MotivationExecution.objects.create(run_id=uuid4(),job_key=job.key,state='completed',started_at=timezone.now(),completed_at=timezone.now(),parameters={'fact_year':2026,'plan_version':'Recorded plan'},export_snapshot={'versions':[{'title':'Recorded coefficients','from':'2026-01-01'}]})
            return run
        run=self.db(setup_run)
        with patch('admin_motivation.operation_views.calculation_jobs.monitor',return_value={'status':'Завершено','history':[]}), patch('admin_motivation.services.execution_tracking.observe',return_value=run):
            self.page.goto(self.live_server_url+reverse('motivation_calculations'))
            link=self.page.locator('[data-job-report]');expect(link).to_be_visible()
            expect(link).to_have_attribute('href',__import__('re').compile('year=2026'))
            with patch('admin_motivation.report_views.choices',return_value=[]),patch('admin_motivation.report_views.load_report',return_value={'currency':'USD','nodes':[],'periods':[]}):
                with self.page.expect_navigation():link.click()
            expect(self.page.locator('.ux-report-receipt')).to_contain_text('Recorded plan')
            expect(self.page.locator('.ux-report-receipt')).to_contain_text('Recorded coefficients')
        self.assertEqual(self.errors,[])

    def test_segments_subdivision_search_and_navigation(self):
        from playwright.sync_api import expect
        def setup_branch():
            channel=Chanel.objects.create(chanel_name='Search nav')
            return Subdivision.objects.create(subdivision_key='NAV',subdivision_name='Д-Москва',subdivision_global='Д-Москва',chanel=channel).pk
        branch=self.db(setup_branch)
        self.page.goto(self.live_server_url + reverse('motivation_discount_segments'))
        labels = self.page.evaluate("""() => [...document.querySelector('#subdivision-nav').closest('ul').children].map(li => li.querySelector(':scope > a,:scope > details > summary').textContent.trim())""")
        self.assertEqual(labels, ['Планы продаж','Сегменты скидок','Глобальные коэффициенты','Подразделения','Отчёты','Выгрузка в DWH'])
        self.page.locator('#subdivision-nav > summary').click()
        self.page.locator('#subdivision-search').fill('Москва')
        link=self.page.locator('#subdivision-links a').filter(has_text='Москва')
        expect(link).to_be_visible()
        expect(self.page.locator('#subdivision-search-empty')).not_to_be_visible()
        with self.page.expect_navigation(): link.click()
        self.assertIn(reverse('coeff_subdivisions_admin_motivation',args=[branch]),self.page.url)
        self.assertEqual(self.errors, [])

    def test_verified_save_receipt_and_stale_tab_rejection(self):
        from playwright.sync_api import expect
        self.page.goto(self.live_server_url + reverse('global_coeff_admin_motivation'))
        cell = self.page.locator(f'input[name="global_coeff={self.coeff.pk}"]')
        cell.fill('25%');cell.press('Tab')
        with self.page.expect_navigation():
            self.page.locator('#editor-save').click()
        expect(self.page.locator('#version-panel')).to_contain_text('Сохранение проверено. Изменено полей:')
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.get(pk=self.coeff.pk).motivation_coeff), .25)
        def another_save():
            row=GlobalCoeff.objects.get(pk=self.coeff.pk)
            row.motivation_coeff=.3;row.save()
        self.db(another_save)
        cell.fill('40%');cell.press('Tab')
        with self.page.expect_navigation():
            self.page.locator('#editor-save').click()
        expect(self.page.locator('#version-panel')).to_contain_text('Версия изменилась')
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.get(pk=self.coeff.pk).motivation_coeff), .3)
        self.assertEqual(self.errors, [])

    def test_admin_calculation_job_create_and_run(self):
        from .models import SalesPlanScenario
        from django.utils import timezone
        for version in ('Selected SQL plan', 'Other SQL plan'):
            self.db(lambda version=version: SalesPlanScenario.objects.create(title=version, source_version=version, loaded_at=timezone.now()))
        from unittest.mock import patch
        from playwright.sync_api import expect
        with patch('admin_motivation.services.calculation_jobs.sync_job') as sync, patch('admin_motivation.services.calculation_jobs.start_job') as start, patch('admin_motivation.services.calculation_jobs.status', return_value='Выполняется'), patch('admin_motivation.services.calculation_jobs.monitor', return_value={'status': 'Выполняется', 'history': []}):
            self.page.goto(self.live_server_url + reverse('motivation_calculations'))
            self.page.locator('#id_name').fill('Browser calculation')
            self.page.locator('#id_fact_year').fill('2026')
            self.page.locator('#id_plan_version').select_option('Selected SQL plan')
            self.page.get_by_role('button', name='Создать задание', exact=True).click()
            expect(self.page.get_by_text('Browser calculation', exact=True)).to_be_visible()
            card = self.page.locator('.calculation-job')
            card.locator('.admin-job-details > summary').click()
            card.locator('.calculation-edit > summary').click()
            card.locator('[name=fact_year]').fill('2025')
            card.locator('[name=plan_version]').select_option('Other SQL plan')
            card.locator('[name=quarter_share]').fill('75')
            card.locator('[name=year_share]').fill('25')
            card.locator('[name=payout_threshold]').fill('0.85')
            card.get_by_role('button', name='Сохранить параметры и расписание', exact=True).click()
            expect(self.page.locator('.calculation-job')).to_contain_text('Год факта: 2025')
            # A running task must not accept another launch from the interface.
            expect(self.page.get_by_role('button', name='Запустить сейчас', exact=True)).to_be_disabled()
            expect(self.page.locator('[data-job-status]')).to_have_text('Выполняется')
            self.assertEqual(sync.call_count, 2)
            start.assert_not_called()
            self.assertEqual(self.errors, [])

    def test_approved_manager_correction_has_reason_confirmation_and_audit(self):
        from playwright.sync_api import expect
        from .models import ManagerCorrectionLog
        from .services.approval import approve_review, review_approval
        def prepare():
            channel = Chanel.objects.create(chanel_name='Correction UI')
            sub = Subdivision.objects.create(subdivision_key='Correction',subdivision_name='Correction',subdivision_global='Correction',chanel=channel)
            kind = KindManagerCoeff.objects.create(name='Год')
            row = SubdivisionManagerCoeff.objects.create(version=self.first,subdivision=sub,kind=kind,coeff=.8)
            approve_review(review_approval(GlobalCoeffVersion.objects.get(pk=self.first.pk))['token'])
            return sub.pk,kind.pk,row.pk
        sub,kind,row = self.db(prepare)
        self.page.goto(self.live_server_url + reverse('coeff_subdivisions_admin_motivation',args=[sub]))
        panel = self.page.locator('#manager-details')
        panel.locator(':scope > summary').click()
        field = panel.locator(f'input[name="kind_coeff={kind}"]')
        expect(field).to_be_enabled()
        field.fill('90%')
        panel.locator('[name=manager_reason]').fill('Итоги квартала')
        panel.locator('[name=confirm_manager_change]').check()
        with self.page.expect_navigation():
            panel.get_by_role('button',name='Сохранить руководителя',exact=True).click()
        self.assertEqual(self.db(lambda: SubdivisionManagerCoeff.objects.get(pk=row).coeff),.9)
        self.assertEqual(self.db(lambda: ManagerCorrectionLog.objects.get().reason),'Итоги квартала')
        self.assertEqual(self.db(lambda: GlobalCoeffVersion.objects.get(pk=self.first.pk).status),'approved')
        panel.locator(':scope > summary').click()
        panel.get_by_text('Журнал изменений поправок',exact=True).click()
        expect(panel.get_by_text('Итоги квартала',exact=False)).to_be_visible()
        expect(self.page.locator('#subCoeffForm fieldset')).to_have_attribute('disabled', '')

    def test_calculation_status_and_history_refresh_without_losing_form_edits(self):
        from unittest.mock import patch
        from playwright.sync_api import expect
        from .models import MotivationCalculationSchedule
        self.db(lambda: MotivationCalculationSchedule.objects.create(name='Live monitor', synced=True))
        snapshot = {'status': 'Выполняется', 'history': []}
        with patch('admin_motivation.services.calculation_jobs.status', return_value='Выполняется'), patch('admin_motivation.services.calculation_jobs.monitor', side_effect=lambda job: snapshot.copy()):
            self.page.clock.install()
            self.page.goto(self.live_server_url + reverse('motivation_calculations'))
            expect(self.page.locator('[data-job-monitor-note]')).to_contain_text('Проверено в')
            self.page.locator('.ux-create-job > summary').click()
            self.page.locator('#id_name').fill('Несохранённый текст')
            self.page.locator('.admin-job-details > summary').click()
            self.page.locator('.calculation-history summary').click()
            snapshot.update(status='Завершено', history=[{'id': 1, 'started': '24.10.2026 12:00:00',
                'duration': '00:05:42', 'status': 'Завершено', 'message': '<script>history message</script>'}])
            self.page.clock.fast_forward(10000)
            expect(self.page.locator('[data-job-status]')).to_have_text('Завершено')
            expect(self.page.locator('[data-job-history]')).to_contain_text('00:05:42')
            expect(self.page.locator('[data-job-history]')).…19449 tokens truncated…nfirm').check()
        with self.page.expect_navigation(): self.page.locator('#approval-commit').click()
        self.assertEqual(self.db(lambda: GlobalCoeffVersion.objects.get(pk=future).status), 'superseded')
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.get(pk=row).motivation_coeff), .5)
        self.assertTrue(self.page.locator(f'input[name="global_coeff={row}"]').is_disabled())
        expect(self.page.locator('[data-amount="approved.total"]')).to_have_text('645')
        self.page.goto(self.live_server_url + reverse('coeff_subdivisions_admin_motivation', args=[sub]) + f'?version={current}')
        self.assertTrue(self.page.locator('#comparison-show-baseline').is_checked())
        self.assertEqual(self.page.locator('#comparison-plan').input_value(), str(plan))
        expect(self.page.locator('[data-amount="current.total"]')).to_have_text('230')
        self.page.locator('#comparison-plan').select_option('')
        expect(self.page.locator('[data-amount="current.total"]')).to_have_text('—')
        self.assertEqual(self.errors, [])

    def test_editor_filters_dirty_state_and_guard_preserve_hidden_edits(self):
        from playwright.sync_api import expect
        def setup_second():
            original = GlobalCoeff.objects.get(pk=self.coeff.pk)
            good = Goods.objects.create(goods_key='OtherKey', planning_group_sales='Other', group='Other ERP', brand='Other brand')
            return GlobalCoeff.objects.create(version=self.first, goods=good, type_coeff=original.type_coeff, segment=original.segment,
                variation_calculate=original.variation_calculate, motivation_coeff=.2, manager_coeff=1).pk
        second = self.db(setup_second)
        self.page.goto(self.live_server_url + reverse('global_coeff_admin_motivation'))
        self.assertFalse(self.page.locator('#create-version-details').evaluate('(element) => element.open'))
        self.assertFalse(self.page.locator('#add-product-details').evaluate('(element) => element.open'))
        expect(self.page.locator('#editor-save')).to_be_disabled()
        first_input = self.page.locator(f'input[name="global_coeff={self.coeff.pk}"]')
        first_input.focus(); first_input.press('Tab')
        expect(self.page.locator('#editor-dirty-count')).to_have_text('Изменено: 0')
        first_input.fill('0.25'); first_input.press('Tab')
        expect(first_input).to_have_class(__import__('re').compile('editor-changed'))
        expect(self.page.locator('#editor-dirty-count')).to_have_text('Изменено: 1')
        self.page.locator('#coefficient-only-changed').check()
        expect(self.page.locator('#coefficient-visible-count')).to_have_text('Показано: 1 из 2')
        dialogs = []
        def cancel_dialog(dialog): dialogs.append(dialog.message); dialog.dismiss()
        self.page.on('dialog', cancel_dialog)
        self.page.locator('.header-brand').click()
        self.assertEqual(len(dialogs), 1)
        self.assertIn('несохранённые', dialogs[0])
        expect(first_input).to_have_value('25%')
        self.page.remove_listener('dialog', cancel_dialog)
        first_input.fill('12.3456%'); first_input.press('Tab')
        expect(self.page.locator('#editor-dirty-count')).to_have_text('Изменено: 0')
        expect(self.page.locator('#coefficient-visible-count')).to_have_text('Показано: 0 из 2')
        self.page.locator('#coefficient-reset').click()
        first_input.fill('0.25'); first_input.press('Tab')
        second_input = self.page.locator(f'input[name="global_coeff={second}"]')
        second_input.fill('0.35'); second_input.press('Tab')
        self.choose_filter('#coefficient-pg', 'Other')
        expect(first_input).not_to_be_visible()
        expect(self.page.locator('#coefficient-visible-count')).to_have_text('Показано: 1 из 2')
        with self.page.expect_navigation(): self.page.locator('#editor-save').click()
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.get(pk=self.coeff.pk).motivation_coeff), .25)
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.get(pk=second).motivation_coeff), .35)
        expect(self.page.locator('#editor-dirty-count')).to_have_text('Изменено: 0')
        self.page.locator('#coefficient-search').fill('OtherKey')
        expect(self.page.locator('#coefficient-visible-count')).to_have_text('Показано: 1 из 2')
        self.page.locator('#coefficient-search').fill('missing')
        expect(self.page.locator('#coefficient-filter-empty')).to_be_visible()
        self.page.locator('#coefficient-reset').click()
        self.choose_filter('#coefficient-group', 'Other ERP')
        expect(self.page.locator('#coefficient-visible-count')).to_have_text('Показано: 1 из 2')
        self.page.locator('#coefficient-reset').click()
        self.choose_filter('#coefficient-brand', 'Other brand')
        expect(self.page.locator('#coefficient-visible-count')).to_have_text('Показано: 1 из 2')
        self.assertEqual(self.errors, [])

    def test_subdivision_navigation_and_copy_confirmation_and_manager_guard(self):
        from playwright.sync_api import expect
        def setup_sub():
            channel = Chanel.objects.create(chanel_name='Dealer')
            subdivisions = [Subdivision.objects.create(subdivision_key=name, subdivision_name=name, subdivision_global=name, chanel=channel) for name in ['A', 'B']]
            original = GlobalCoeff.objects.get(pk=self.coeff.pk)
            row = SubdivisionCoeff.objects.create(version=self.first, subdivision=subdivisions[0], goods=original.goods,
                type_coeff=original.type_coeff, segment=original.segment, variation_calculate=original.variation_calculate, motivation_coeff=.15, manager_coeff=1)
            manager = SubdivisionManagerCoeff.objects.create(version=self.first, subdivision=subdivisions[0], kind=KindManagerCoeff.objects.create(name='Year'), coeff=.8)
            return subdivisions[0].pk, row.pk, manager.pk
        sub, row, manager = self.db(setup_sub)
        self.page.goto(self.live_server_url + reverse('global_coeff_admin_motivation'))
        self.page.locator('#subdivision-nav summary').focus()
        self.page.locator('#subdivision-nav summary').press('Enter')
        self.page.locator('#subdivision-search').fill('A')
        expect(self.page.locator('#subdivision-links a:visible')).to_have_count(1)
        with self.page.expect_navigation(): self.page.locator('#subdivision-links a:visible').click()
        self.assertIn(f'/{sub}', self.page.url)
        self.assertFalse(self.page.locator('#manager-details').evaluate('(element) => element.open'))
        self.page.locator('#manager-details summary').click()
        self.page.locator('input[name^="kind_coeff="]').fill('0.9')
        self.page.locator('input[name^="kind_coeff="]').press('Tab')
        expect(self.page.locator('#editor-save')).to_be_disabled()
        self.page.locator('#approval-review').click()
        expect(self.page.locator('#editor-error')).to_contain_text('Сначала сохраните')
        expect(self.page.locator('#approval-dialog')).not_to_be_visible()
        with self.page.expect_navigation(): self.page.get_by_role('button', name='Сохранить руководителя', exact=True).click()
        self.assertEqual(self.db(lambda: SubdivisionManagerCoeff.objects.get(pk=manager).coeff), .9)
        local_input = self.page.locator(f'input[name="sub_coeff={row}"]')
        local_input.fill('.25'); local_input.press('Tab')
        self.page.get_by_role('button',name='Импорт и справочники',exact=True).click()
        dialogs = []
        def cancel(dialog): dialogs.append(dialog.message); dialog.dismiss()
        self.page.on('dialog', cancel)
        self.page.get_by_role('button', name='Копировать из глобальных', exact=True).click()
        self.page.get_by_role('button', name='Скопировать выбранную группу', exact=True).click()
        self.assertEqual(len(dialogs), 1)
        self.assertIn('Заменить коэффициенты группы', dialogs[0])
        self.assertEqual(self.db(lambda: SubdivisionCoeff.objects.get(pk=row).motivation_coeff), .15)
        self.page.remove_listener('dialog', cancel)
        self.page.once('dialog', lambda dialog: dialog.accept())
        with self.page.expect_navigation(): self.page.get_by_role('button', name='Скопировать выбранную группу', exact=True).click()
        self.assertEqual(self.db(lambda: SubdivisionCoeff.objects.get(version=self.first, subdivision_id=sub).motivation_coeff), .123456)
        expect(self.page.locator('#editor-dirty-count')).to_have_text('Изменено: 0')
        self.assertEqual(self.errors, [])

    def test_director_forms_copy_global_and_save_individual_percentages(self):
        from playwright.sync_api import expect
        from decimal import Decimal
        from .models import GlobalDirectorCoeff, SubdivisionDirectorCoeff, DirectorEmployee
        def setup():
            channel = Chanel.objects.create(chanel_name='Director channel')
            sub = Subdivision.objects.create(subdivision_key='Director branch', subdivision_name='Director branch', subdivision_global='Director branch', chanel=channel)
            employee = DirectorEmployee.objects.create(full_name='Директор один', employee_code='DIR-1')
            other = DirectorEmployee.objects.create(full_name='Директор два', employee_code='DIR-2')
            employee.subdivisions.add(sub)
            other.subdivisions.add(sub)
            GlobalDirectorCoeff.objects.create(version=self.first, goods=self.coeff.goods, k0=Decimal('.123456'), k1=Decimal('.02'))
            return sub.pk, employee.pk, other.pk
        sub, employee, other = self.db(setup)
        self.page.goto(self.live_server_url + reverse('motivation_directors_global'))
        expect(self.page.get_by_label('Директор K0', exact=True)).to_have_value('12.3456%')
        self.page.goto(self.live_server_url + reverse('motivation_directors_local') + f'?subdivision={sub}&employee={employee}')
        self.page.get_by_role('button',name='Импорт и справочники',exact=True).click()
        self.page.get_by_role('button', name='Копировать из глобальных', exact=True).click()
        self.page.locator('#copy-coefficient-group').select_option('directors')
        self.page.once('dialog', lambda dialog: dialog.accept())
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Скопировать выбранную группу', exact=True).click()
        expect(self.page.get_by_label('Директор K0', exact=True)).to_have_value('12.3456%')
        self.page.get_by_label('Директор K0', exact=True).fill('25%')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Сохранить изменения', exact=True).click()
        expect(self.page.locator('#version-panel')).to_contain_text('Сохранение проверено')
        expect(self.page.get_by_label('Директор K0', exact=True)).to_have_value('25%')
        self.assertEqual(self.db(lambda: SubdivisionDirectorCoeff.objects.get(employee_id=employee).k0), Decimal('.25'))
        self.assertFalse(self.db(lambda: SubdivisionDirectorCoeff.objects.filter(employee_id=other).exists()))
        self.assertEqual(self.db(lambda: GlobalDirectorCoeff.objects.get().k0), Decimal('.123456'))
        self.assertEqual(self.errors, [])

    def test_shared_director_columns_visibility_and_copy_group_choice(self):
        from playwright.sync_api import expect
        from decimal import Decimal
        from .models import GlobalDirectorCoeff, SubdivisionDirectorCoeff, DirectorEmployee
        def setup():
            channel = Chanel.objects.create(chanel_name='Group copy channel')
            sub = Subdivision.objects.create(subdivision_key='Group copy branch', subdivision_name='Group copy branch', subdivision_global='Group copy branch', chanel=channel)
            employee = DirectorEmployee.objects.create(full_name='Директор группы', employee_code='GROUP-DIR')
            employee.subdivisions.add(sub)
            GlobalDirectorCoeff.objects.create(version=self.first, goods=self.coeff.goods, k0=Decimal('.03'))
            return sub.pk, employee.pk
        sub, employee = self.db(setup)
        url = self.live_server_url + reverse('global_coeff_admin_motivation')
        self.page.goto(url)
        expect(self.page.get_by_label('Директор K0', exact=True)).to_have_value('3%')
        self.page.locator('[data-coefficient-group-toggle="directors"]').uncheck()
        expect(self.page.get_by_label('Директор K0', exact=True)).not_to_be_visible()
        self.page.reload()
        expect(self.page.locator('[data-coefficient-group-toggle="directors"]')).not_to_be_checked()
        self.page.locator('[data-coefficient-group-toggle="directors"]').check()
        self.page.get_by_label('Директор K0', exact=True).fill('7%')
        self.page.get_by_label('Директор K0', exact=True).dispatch_event('change')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Сохранить изменения', exact=True).click()
        self.assertEqual(self.db(lambda: GlobalDirectorCoeff.objects.get().k0), Decimal('.07'))
        self.page.goto(self.live_server_url + reverse('coeff_subdivisions_admin_motivation', args=[sub]))
        self.page.get_by_role('button',name='Импорт и справочники',exact=True).click()
        self.page.get_by_role('button', name='Копировать из глобальных', exact=True).click()
        expect(self.page.locator('#copy-global-dialog')).to_be_visible()
        self.page.locator('#copy-coefficient-group').select_option('directors')
        self.page.locator('#copy-director-select').select_option(str(employee))
        self.page.once('dialog', lambda dialog: dialog.accept())
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Скопировать выбранную группу', exact=True).click()
        self.assertEqual(self.db(lambda: SubdivisionDirectorCoeff.objects.get(employee_id=employee).k0), Decimal('.07'))
        self.assertIn(reverse('coeff_subdivisions_admin_motivation', args=[sub]), self.page.url)
        self.assertEqual(self.errors, [])

    def test_local_employee_change_warns_and_copy_to_another_director(self):
        from playwright.sync_api import expect
        from decimal import Decimal
        from .models import DirectorEmployee, SubdivisionDirectorCoeff
        def setup():
            channel = Chanel.objects.create(chanel_name='Switch director channel')
            sub = Subdivision.objects.create(subdivision_key='Switch branch', subdivision_name='Switch branch', subdivision_global='Switch branch', chanel=channel)
            first = DirectorEmployee.objects.create(full_name='Первый', employee_code='SW-1')
            second = DirectorEmployee.objects.create(full_name='Второй', employee_code='SW-2')
            for employee in (first, second):
                employee.subdivisions.add(sub)
                SubdivisionDirectorCoeff.objects.create(version=self.first, subdivision=sub, employee=employee,
                    goods=self.coeff.goods, k0=Decimal('.1') if employee.pk == first.pk else Decimal('.2'))
            return sub.pk, first.pk, second.pk
        sub, first, second = self.db(setup)
        url = self.live_server_url + reverse('coeff_subdivisions_admin_motivation', args=[sub]) + f'?employee={first}'
        self.page.goto(url)
        expect(self.page.get_by_label('Директор K0', exact=True)).to_have_value('10%')
        self.page.get_by_label('Директор K0', exact=True).fill('15%')
        self.page.once('dialog', lambda dialog: dialog.dismiss())
        self.choose_filter('#director-employee-choice', str(second))
        expect(self.page.locator('#director-employee-choice')).to_have_value(str(first))
        expect(self.page.get_by_label('Директор K0', exact=True)).to_have_value('15%')
        self.page.once('dialog', lambda dialog: dialog.accept())
        with self.page.expect_navigation():
            self.choose_filter('#director-employee-choice', str(second))
        expect(self.page.get_by_label('Директор K0', exact=True)).to_have_value('20%')
        self.page.get_by_role('button',name='Импорт и справочники',exact=True).click()
        self.page.get_by_role('button', name='Копировать другому директору', exact=True).click()
        self.page.locator('[name=copy_to_employee]').select_option(str(first))
        self.page.once('dialog', lambda dialog: dialog.accept())
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Скопировать коэффициенты', exact=True).click()
        self.assertEqual(self.db(lambda: SubdivisionDirectorCoeff.objects.get(employee_id=first).k0), Decimal('.2'))
        expect(self.page.locator('#director-employee-choice')).to_have_value(str(first))
        self.assertEqual(self.errors, [])

    def test_pilot_dashboard_expansion_keeps_headers_and_rows_aligned(self):
        from playwright.sync_api import expect
        from .services.reports import manager_report
        from .services.report_periods import PILOT_QUARTER
        from .test_reports import NativeMotivationReportTests
        rows = [dict(NativeMotivationReportTests.manager_rows()[0], КварталМотивации=p)
                for p in ('1 Квартал', PILOT_QUARTER, 'Год')]
        report = manager_report(rows, pilot=True)
        with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', return_value=report):
            self.page.goto(self.live_server_url + reverse('motivation_report', args=['manager']) + '?run=1&quarter=4+Квартал')
            dashboard = self.page.locator('#manager-dashboard')
            expect(dashboard).to_have_attribute('data-year-period-count', '2')
            for reveal in ('year-possible', 'year-available', 'year-period', 'year-motivation'):
                dashboard.locator('.report-column-header [data-dashboard-reveal="' + reveal + '"]').click()
            self.assertEqual(dashboard.evaluate('(el) => el.style.getPropertyValue("--dashboard-year")'), '14')
            counts = dashboard.locator('.report-column-header, .report-total-row, .division-percent').evaluate_all(
                '(rows) => rows.map(row => Array.from(row.children).filter(cell => !cell.hidden).length)')
            self.assertEqual(len(set(counts)), 1)
            expect(dashboard.locator('.report-column-header [data-dashboard-column="year-possible"]:visible')).to_have_count(2)
            self.page.screenshot(path='/tmp/pilot-dashboard.png', full_page=True)
        self.assertEqual(self.errors, [])

    def test_native_reports_render_manager_and_lazy_subdivision_tree(self):
        from unittest.mock import patch
        from playwright.sync_api import expect
        from .test_reports import NativeMotivationReportTests
        from .services.reports import manager_report, subdivision_report
        manager=manager_report(NativeMotivationReportTests.manager_rows())
        division=subdivision_report(NativeMotivationReportTests.division_rows())
        with patch('admin_motivation.report_views.choices',return_value=[]), patch('admin_motivation.report_views.load_report',return_value=manager):
            self.page.goto(self.live_server_url + reverse('motivation_report',args=['manager']) + '?run=1&type=Политики&subdivision=Филиал&author=Менеджер&quarter=1+Квартал')
            expect(self.page.locator('.report-title')).to_have_text('Мотивация. Дашборд · USD')
            expect(self.page.locator('.manager-section').nth(1)).not_to_have_attribute('open','')
            self.page.locator('.manager-section').nth(1).locator('summary').click()
            expect(self.page.get_by_text('4. VENT',exact=True)).to_be_visible()
            expect(self.page.locator('.manager-grid.report-total-row')).to_contain_text('360')
            self.page.screenshot(path='/tmp/native-manager-report.png',full_page=True)
        with patch('admin_motivation.report_views.choices',return_value=[]), patch('admin_motivation.report_views.load_report',return_value=division):
            self.page.goto(self.live_server_url + reverse('motivation_report',args=['subdivisions']) + '?run=1')
            self.assertEqual(self.page.locator('.native-division-node').count(),1)
            self.assertEqual(self.page.locator('.native-division-leaf').count(),0)
            self.page.get_by_role('button',name='Развернуть всё',exact=True).click()
            expect(self.page.locator('.native-division-leaf')).to_be_visible()
            expect(self.page.locator('.native-division-leaf')).to_contain_text('Brand')
            root = self.page.locator('#native-division-report')
            expect(root.locator('.report-column-header [data-division-bonus]')).to_have_count(1)
            expect(root.locator('.report-total-row [data-division-bonus]')).to_have_count(1)
            expect(root.locator('.native-division-leaf [data-division-bonus]')).to_have_count(1)
            expect(root.locator('[data-division-motivation]:visible')).to_have_count(0)
            expect(root.locator('[data-division-segments]:visible')).to_have_count(0)
            expect(root.get_by_text('План периода', exact=True).first).to_be_visible()
            expect(root.get_by_text('% вып. периода', exact=True).first).to_be_visible()
            root.locator('.report-column-header [data-division-reveal="motivation"]').first.click()
            expect(self.page.locator('.native-division-leaf [data-division-motivation]').first).to_be_visible()
            root.locator('.report-column-header [data-division-reveal="segments"]').first.click()
            expect(self.page.locator('.native-division-leaf [data-division-segments]').first).to_be_visible()
            self.assertEqual(root.evaluate('(el) => el.style.getPropertyValue("--report-columns")'), '15')
            widths = root.locator('.report-period-header > strong').evaluate_all('(els) => els.map(el => el.getBoundingClientRect().width)')
            self.assertEqual(widths[-1] - widths[0], 112)
            counts = root.locator('.report-column-header, .report-total-row, .native-division-leaf').evaluate_all('(els) => els.map(el => Array.from(el.children).filter(child => !child.hidden).length)')
            self.assertEqual(len(set(counts)), 1)
            root.locator('.native-division-node > summary [data-division-reveal="segments"]').first.click()
            expect(root.locator('[data-division-segments]:visible')).to_have_count(0)
            expect(root.locator('.native-division-node').first).to_have_attribute('open', '')
            self.page.get_by_role('button',name='Свернуть всё',exact=True).click()
            self.assertEqual(self.page.locator('.native-division-node[open]').count(),0)
        self.assertEqual(self.errors,[])

    def test_bulk_multiply_common_segments_validation_undo_and_save(self):
        from playwright.sync_api import expect
        from .models import GlobalDirectorCoeff
        def fixture():
            original=GlobalCoeff.objects.get(pk=self.coeff.pk)
            original.segment.segment_name='K0';original.segment.save()
            original.motivation_coeff=.01;original.save()
            rows=[original]
            for index in range(1,5):
                segment=SegmentCoeff.objects.create(segment_name=f'K{index}')
                rows.append(GlobalCoeff.objects.create(version=self.first,goods=original.goods,type_coeff=original.type_coeff,segment=segment,motivation_coeff=.01*(index+1),manager_coeff=1,variation_calculate=original.variation_calculate))
            director=GlobalDirectorCoeff.objects.create(version=self.first,goods=original.goods,**{f'k{i}':'.01' for i in range(5)})
            return original.goods_id,[row.pk for row in rows],director.pk
        good,ids,director=self.db(fixture)
        self.page.goto(self.live_server_url+reverse('global_coeff_admin_motivation'))
        self.page.get_by_role('button',name='Изменить ставки',exact=True).click()
        self.page.locator(f'.bulk-good[value="{good}"]').check()
        self.page.locator('#multiply-common').fill('1,1')
        self.page.locator('#multiply-preview').click()
        expect(self.page.locator('#multiply-review')).to_be_visible()
        self.assertEqual(self.db(lambda:GlobalCoeff.objects.get(pk=ids[0]).motivation_coeff),.01)
        self.page.locator('#multiply-apply').click()
        first=self.page.locator(f'[name="global_coeff={ids[0]}"]')
        expect(first).to_have_value('1.1%')
        self.page.locator('#multiply-undo').click();expect(first).to_have_value('1%')
        self.page.locator('#multiply-mode').select_option('segments')
        for i,value in enumerate(['2','1','0.5','1','0']):self.page.locator(f'[data-multiply-segment="{i}"]').fill(value)
        self.page.locator('#multiply-preview').click();self.page.locator('#multiply-apply').click()
        expect(first).to_have_value('2%')
        expect(self.page.locator(f'[name="global_coeff={ids[2]}"]')).to_have_value('1.5%')
        expect(self.page.locator(f'[name="global_coeff={ids[4]}"]')).to_have_value('0%')
        self.page.locator('#multiply-mode').select_option('common');self.page.locator('#multiply-common').fill('1000')
        self.page.locator('#multiply-preview').click();expect(self.page.locator('#multiply-error')).to_contain_text('диапазон')
        expect(first).to_have_value('2%')
        self.page.locator('[data-multiply-kind="policies"]').uncheck();self.page.locator('[data-multiply-kind="directors"]').check()
        self.page.locator('#multiply-common').fill('2');self.page.locator('#multiply-preview').click();self.page.locator('#multiply-apply').click()
        expect(self.page.locator(f'[name="director_coeff={good}=4"]')).to_have_value('2%')
        with self.page.expect_navigation():self.page.locator('#editor-save').click()
        self.assertEqual(self.db(lambda:GlobalCoeff.objects.get(pk=ids[0]).motivation_coeff),.02)
        self.assertEqual(self.db(lambda:GlobalCoeff.objects.get(pk=ids[4]).motivation_coeff),0)
        self.assertEqual(str(self.db(lambda:GlobalDirectorCoeff.objects.get(pk=director).k4)),'0.020000')
        self.assertEqual(self.errors,[])

    def test_local_bulk_multiply_changes_only_selected_director(self):
        from playwright.sync_api import expect
        from .models import DirectorEmployee, SubdivisionDirectorCoeff
        def fixture():
            original=GlobalCoeff.objects.get(pk=self.coeff.pk)
            original.segment.segment_name='K0';original.segment.save()
            channel=Chanel.objects.create(chanel_name='Multiply local')
            sub=Subdivision.objects.create(subdivision_key='Multiply branch',subdivision_name='Multiply branch',subdivision_global='Multiply branch',chanel=channel)
            SubdivisionCoeff.objects.create(version=self.first,goods=original.goods,type_coeff=original.type_coeff,segment=original.segment,motivation_coeff=.02,manager_coeff=1,variation_calculate=original.variation_calculate,subdivision=sub)
            employees=[DirectorEmployee.objects.create(full_name=name) for name in ['Multiply first','Multiply second']]
            for employee in employees:
                employee.subdivisions.add(sub)
                SubdivisionDirectorCoeff.objects.create(version=self.first,goods=original.goods,subdivision=sub,employee=employee,**{f'k{i}':'.01' for i in range(5)})
            return sub.pk,employees[0].pk,employees[1].pk,original.goods_id
        branch,first,second,good=self.db(fixture)
        self.page.goto(self.live_server_url+reverse('coeff_subdivisions_admin_motivation',args=[branch])+f'?employee={first}')
        self.page.get_by_role('button',name='Изменить ставки',exact=True).click()
        self.page.locator(f'.bulk-good[value="{good}"]').check()
        self.page.locator('[data-multiply-kind="policies"]').uncheck();self.page.locator('[data-multiply-kind="directors"]').check()
        self.page.locator('#multiply-common').fill('2');self.page.locator('#multiply-preview').click();self.page.locator('#multiply-apply').click()
        expect(self.page.locator(f'[name="director_coeff={good}=0"]')).to_have_value('2%')
        with self.page.expect_navigation():self.page.locator('#editor-save').click()
        self.assertEqual(str(self.db(lambda:SubdivisionDirectorCoeff.objects.get(employee_id=first).k0)),'0.020000')
        self.assertEqual(str(self.db(lambda:SubdivisionDirectorCoeff.objects.get(employee_id=second).k0)),'0.010000')
        self.assertEqual(self.db(lambda:SubdivisionCoeff.objects.get(subdivision_id=branch).motivation_coeff),.02)
        self.assertEqual(self.errors,[])

    def test_reapprove_after_reopen_preserves_prior_approval(self):
        from playwright.sync_api import expect
        from .services.approval import approve_review,review_approval
        from .models import MotivationApproval
        first_log=self.db(lambda:approve_review(review_approval(GlobalCoeffVersion.objects.get(pk=self.first.pk))['token']).approval.pk)
        self.page.goto(self.live_server_url+reverse('global_coeff_admin_motivation'))
        self.page.locator('.version-tools button[aria-controls="version-reopen"]').click()
        self.page.locator('#version-reopen [name=reason]').fill('Browser reapproval')
        self.page.locator('#version-reopen [name=confirm]').check()
        with self.page.expect_navigation():self.page.locator('#version-reopen button[type=submit]').click()
        self.page.locator('#approval-review').click()
        expect(self.page.locator('#approval-commit')).to_be_enabled()
        self.page.locator('#approval-reason').fill('Пилот: проверка администратором')
        with self.page.expect_navigation():self.page.locator('#approval-commit').click()
        expect(self.page.locator('#version-approval-actions')).to_contain_text('Утверждена')
        self.assertEqual(self.db(lambda:MotivationApproval.objects.filter(version_id=self.first.pk).count()),2)
        self.assertTrue(self.db(lambda:MotivationApproval.objects.filter(pk=first_log).exists()))
        self.assertEqual(self.errors,[])

    def test_director_import_template_button_downloads_xlsx(self):
        from playwright.sync_api import expect
        from openpyxl import load_workbook
        self.page.goto(self.live_server_url+reverse('motivation_director_coeff_import')+f'?version={self.first.pk}')
        button=self.page.get_by_role('link',name='Скачать шаблон Excel',exact=True)
        expect(button).to_be_visible()
        with self.page.expect_download() as event:button.click()
        download=event.value
        self.assertEqual(download.suggested_filename,f'director-coefficients-v{self.first.pk}.xlsx')
        download.save_as('/tmp/director-import-template-browser.xlsx')
        book=load_workbook('/tmp/director-import-template-browser.xlsx')
        self.assertIn('Коэффициенты',book.sheetnames)
        self.assertIn('Инструкция',book.sheetnames)
        self.assertEqual(book['Коэффициенты']['E5'].value,'K0')
        book.close()
        self.assertEqual(self.errors,[])

    def test_subdivision_premium_filter_year_header_and_excel_downloads(self):
        from playwright.sync_api import expect
        from openpyxl import load_workbook
        from .services.reports import PREMIUM_PERIODS, subdivision_report
        from .test_subdivision_report_export import SubdivisionReportExportTests
        base = SubdivisionReportExportTests.rows()[0]
        rows = [dict(base, КварталМотивации=f'{quarter} Квартал') for quarter in range(1, 5)] + [dict(base, КварталМотивации='Год')]
        def options(kind, values, **kwargs):
            return [{'name': 'period', 'label': 'Период премии', 'multiple': True,
                'options': [{'value': code, 'label': label, 'selected': code in values.get('PremiumPeriods', [])} for code, label in PREMIUM_PERIODS]}]
        def report(kind, values, page, scope, **kwargs):
            selected = values.get('PremiumPeriods')
            subset = [row for row in rows if not selected or ('5' if row['КварталМотивации'] == 'Год' else row['КварталМотивации'][0]) in selected]
            return subdivision_report(subset, scope)
        url = self.live_server_url + reverse('motivation_report', args=['subdivisions'])
        with patch('admin_motivation.report_views.choices', side_effect=options), patch('admin_motivation.report_views.load_report', side_effect=report):
            self.page.goto(url)
            dropdown = self.page.locator('details[data-field=period]')
            dropdown.locator('summary').click()
            dropdown.get_by_label('4 квартал', exact=True).check()
            dropdown.get_by_role('button', name='Применить', exact=True).click()
            with self.page.expect_navigation():
                self.page.get_by_role('button', name='Сформировать отчёт', exact=True).click()
            expect(self.page.locator('h1.report-title')).to_contain_text('Год расчёта: 2026')
            expect(self.page.locator('#subdivision-summary-table thead')).to_contain_text('4 Квартал')
            expect(self.page.locator('#subdivision-summary-table thead')).not_to_contain_text('1 Квартал')
            self.assertIn('period=4', self.page.url)
            for label, count in [('Скачать весь отчёт Excel', 3), ('Скачать итоговую таблицу Excel', 1), ('Скачать детализацию Excel', 1)]:
                with self.page.expect_download() as event:
                    self.page.get_by_role('link', name=label, exact=True).click()
                download = event.value
                path = '/tmp/' + download.suggested_filename
                download.save_as(path)
                book = load_workbook(path)
                self.assertEqual(len(book.sheetnames), count)
                self.assertIn('4 квартал', book.active['A2'].value)
                book.close()
            self.page.screenshot(path='/tmp/subdivision-report-premium-period.png', full_page=True)
            self.page.wait_for_load_state('networkidle')
        self.assertEqual(self.errors, [])

    def test_version_fallback_setting_and_plan_coverage(self):
        from playwright.sync_api import expect
        from django.utils import timezone
        from .models import SalesPlanScenario, SalesPlanLine
        from .services.pilot_formula import GLOBAL_SUBDIVISION
        form_url = reverse('motivation_fallback_formula')
        self.page.goto(self.live_server_url + reverse('global_coeff_admin_motivation'))
        form = self.page.locator(f'form[action="{form_url}"]')
        form.locator('select[name=fallback_variation]').select_option(GLOBAL_SUBDIVISION)
        with self.page.expect_navigation():
            form.get_by_role('button', name='Сохранить формулу по умолчанию').click()
        self.assertEqual(self.db(lambda: GlobalCoeffVersion.objects.get(pk=self.first.pk).fallback_variation), GLOBAL_SUBDIVISION)
        def create_plan():
            channel = Chanel.objects.create(chanel_name='Coverage channel')
            branch = Subdivision.objects.create(subdivision_key='Coverage-A', subdivision_name='Coverage-A', subdivision_global='Coverage-A', chanel=channel)
            plan = SalesPlanScenario.objects.create(title='Coverage plan', source_version='coverage', loaded_at=timezone.now())
            SalesPlanLine.objects.create(scenario=plan, plan_date=date(2026, 1, 1), subdivision=branch.subdivision_key,
                planning_group_sales='Missing planning group', group='Missing group', brand='Missing brand', amount_usd=1000, segment_amounts={'O0': '1000'})
            return plan.pk
        plan = self.db(create_plan)
        self.page.goto(self.live_server_url + reverse('motivation_coefficient_coverage') + f'?version={self.first.pk}&plan={plan}&run=1')
        expect(self.page.get_by_role('heading', name='Покрытие коэффициентами')).to_be_visible()
        expect(self.page.locator('tbody')).to_contain_text('Нет точного коэффициента')
        expect(self.page.locator('tbody')).to_contain_text(GLOBAL_SUBDIVISION)
        self.page.screenshot(path='/tmp/coefficient-fallback-coverage.png', full_page=True)
        self.page.wait_for_load_state('networkidle')
        self.assertEqual(self.errors, [])

    def test_unallocated_author_hidden_toggle_preserves_totals(self):
        from playwright.sync_api import expect
        from .test_manager_detail import detail_rows
        from .services.manager_detail import manager_detail_report
        rows = detail_rows()
        rows += [dict(row, АвторСделки=' ПОЛИТИКИ ') for row in detail_rows()]
        data = manager_detail_report(rows)
        with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', return_value=data):
            self.page.goto(self.live_server_url + reverse('motivation_report', args=['manager-detail']) + '?run=1&type=Политики&details=1')
            hidden = self.page.locator('[data-manager-unallocated]')
            expect(hidden).to_have_count(2)
            for row in hidden.all():
                expect(row).to_be_hidden()
            before = self.page.locator('.manager-period-summary .report-total-row').first.inner_text()
            self.page.locator('#manager-show-unallocated').check()
            for row in hidden.all():
                expect(row).to_be_visible()
            self.assertEqual(self.page.locator('.manager-period-summary .report-total-row').first.inner_text(), before)
            self.page.locator('#manager-show-unallocated').uncheck()
            self.page.locator('#manager-detail-expand').click()
            for row in hidden.all():
                expect(row).to_be_hidden()
        self.assertEqual(self.errors, [])

    def test_first_quarter_inputs_equal_period_fact(self):
        from playwright.sync_api import expect
        from .test_manager_detail import detail_rows
        from .services.manager_detail import manager_detail_report
        from .services.pilot_formula import GLOBAL_SUBDIVISION
        rows = [dict(row, VariationCalculate=GLOBAL_SUBDIVISION) for row in detail_rows()]
        performance = [dict(row, СуммаОтгрузокUSDКвартал=6469139, СуммаПланUSD=235214543, СуммаОтгрузокUSD=235214543) for row in rows]
        data = manager_detail_report(rows, performance_rows=performance)
        with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', return_value=data):
            self.page.goto(self.live_server_url + reverse('motivation_report', args=['manager-detail']) + '?run=1&type=Политики&details=1')
            department = self.page.locator('.manager-division-execution')
            expect(department.locator('[data-manager-reveal=motivation]').first).to_have_text('100,00%')
            department.locator('[data-manager-reveal=motivation]').first.click()
            for index in (0, 1):
                value = department.locator('[data-manager-motivation]').nth(index)
                expect(value).to_be_visible()
                self.assertEqual(value.inner_text().replace('\xa0', ' '), '6 469 139')
        self.assertEqual(self.errors, [])

    def test_global_subdivision_detail_execution_and_excel(self):
        from playwright.sync_api import expect
        from openpyxl import load_workbook
        from .test_manager_detail import detail_rows
        from .services.manager_detail import manager_detail_report
        from .services.pilot_formula import GLOBAL_SUBDIVISION
        rows = [dict(row, VariationCalculate=GLOBAL_SUBDIVISION,
                     КварталМотивации='2 Квартал(1-2 кв)' if row['КварталМотивации'] == '1 Квартал' else row['КварталМотивации'])
                for row in detail_rows()]
        performance = [dict(row, СуммаПланUSD=1000, СуммаОтгрузокUSD=628.4) for row in rows]
        def result(*args, **kwargs):
            return manager_detail_report(rows, performance_rows=performance)
        with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', side_effect=result):
            self.page.goto(self.live_server_url + reverse('motivation_report', args=['manager-detail']) + '?run=1&type=Политики&details=1')
            self.page.locator('#manager-detail-expand').click()
            expect(self.page.locator('#manager-detail-nodes .motivation-execution').first).to_contain_text('62,84%')
            first = self.page.locator('#manager-detail-nodes summary.division-report-grid').first
            expect(first.locator('[data-manager-period-input]').first).to_have_text('—')
            expect(first.locator('[data-manager-reveal=period-input]').first).to_have_text('—')
            department = self.page.locator('.manager-division-execution')
            department.locator('[data-manager-reveal=period-input]').first.click()
            expect(department.locator('[data-manager-period-input]').first).to_be_visible()
            self.assertEqual(department.locator('[data-manager-period-input]').first.inner_text().replace('\xa0', ' '), '1 000')
            department.locator('[data-manager-reveal=motivation]').first.click()
            expect(department.locator('[data-manager-motivation]').first).to_be_visible()
            self.assertEqual(department.locator('[data-manager-motivation]').first.inner_text().replace('\xa0', ' '), '1 000')
            with self.page.expect_download() as event:
                self.page.get_by_role('link', name='Скачать расширенную таблицу Excel').click()
            event.value.save_as('/tmp/global-subdivision-execution.xlsx')
            book = load_workbook('/tmp/global-subdivision-execution.xlsx')
            sheet = book.active
            # First period: plan absent; own fact and branch motivation inputs retained.
            self.assertEqual(sheet.cell(3, 12).value, 1000)
            self.assertEqual(sheet.cell(3, 13).value, 800)
            self.assertEqual(sheet.cell(3, 16).value, 1000)
            self.assertAlmostEqual(sheet.cell(3, 17).value, 628.4)
            self.assertIsNone(sheet.cell(4, 12).value)
            self.assertEqual(sheet.cell(4, 16).value, 1000)
            self.assertAlmostEqual(sheet.cell(4, 18).value, .6284)
            book.close()
            self.page.screenshot(path='/tmp/global-subdivision-detail-execution.png', full_page=True)
            self.page.wait_for_load_state('networkidle')
        self.assertEqual(self.errors, [])


    def test_rate_editor_set_relative_points_diff_reset_and_saved_journal(self):
        from playwright.sync_api import expect
        from .models import CoefficientEditLog, GlobalDirectorCoeff
        def fixture():
            row=GlobalCoeff.objects.get(pk=self.coeff.pk)
            row.segment.segment_name='K0';row.segment.save();row.motivation_coeff=.002;row.save()
            segment=SegmentCoeff.objects.create(segment_name='K1')
            other=GlobalCoeff.objects.create(version=self.first,goods=row.goods,type_coeff=row.type_coeff,segment=segment,motivation_coeff=.006,manager_coeff=1,variation_calculate=row.variation_calculate)
            return row.goods_id,other.pk
        good,other=self.db(fixture)
        self.page.goto(self.live_server_url+reverse('global_coeff_admin_motivation'))
        first=self.page.locator(f'[name="global_coeff={self.coeff.pk}"]')
        self.page.get_by_role('button',name='Изменить ставки',exact=True).click()
        self.page.locator(f'.bulk-good[value="{good}"]').check()
        for index in range(1,5): self.page.locator(f'[data-multiply-step="{index}"]').uncheck()
        def apply(operation,value):
            self.page.locator('#multiply-operation').select_option(operation)
            self.page.locator('#multiply-common').fill(value)
            self.page.locator('#multiply-preview').click()
            expect(self.page.locator('#multiply-review')).to_be_visible()
            self.page.locator('#multiply-apply').click()
        apply('set','0,40%');expect(first).to_have_value('0.4%')
        expect(self.page.locator(f'[name="global_coeff={other}"]')).to_have_value('0.6%')
        apply('relative','10');expect(first).to_have_value('0.44%')
        self.page.locator('#editor-undo').click();expect(first).to_have_value('0.4%')
        apply('points','0,10');expect(first).to_have_value('0.5%')
        self.assertEqual(self.db(lambda:GlobalCoeff.objects.get(pk=self.coeff.pk).motivation_coeff),.002)
        self.page.locator('#editor-review-changes').click()
        expect(self.page.locator('#editor-changes-rows tr')).to_have_count(1)
        expect(self.page.locator('#editor-changes-rows')).to_contain_text('0,2%')
        expect(self.page.locator('#editor-changes-rows')).to_contain_text('0,5%')
        self.page.get_by_role('button',name='Вернуть исходное',exact=True).click()
        expect(self.page.locator('#editor-changes-rows tr')).to_have_count(0)
        self.page.locator('#editor-changes-close').click();expect(first).to_have_value('0.2%')
        self.page.locator('#editor-undo').click();expect(first).to_have_value('0.5%')
        self.page.locator('#editor-reset-changes').click();self.page.locator('#editor-reset-confirm').click()
        expect(first).to_have_value('0.2%')
        expect(self.page.locator('#editor-save')).to_be_disabled()
        self.page.locator('#editor-undo').click();expect(first).to_have_value('0.5%')
        with self.page.expect_navigation():self.page.locator('#editor-save').click()
        self.assertEqual(self.db(lambda:GlobalCoeff.objects.get(pk=self.coeff.pk).motivation_coeff),.005)
        self.assertEqual(self.db(lambda:CoefficientEditLog.objects.count()),1)
        self.assertFalse(self.db(lambda:GlobalDirectorCoeff.objects.exists()))
        expect(self.page.locator('#editor-dirty-count')).to_have_text('Изменено: 0')
        self.page.screenshot(path='/tmp/coefficient-rate-editor.png',full_page=True)
        self.assertEqual(self.errors,[])


    def test_rate_editor_global_exact_copy_and_hidden_filter_selection(self):
        from playwright.sync_api import expect
        def fixture():
            original=GlobalCoeff.objects.get(pk=self.coeff.pk);original.segment.segment_name='K0';original.segment.save();original.motivation_coeff=.002;original.save()
            channel=Chanel.objects.create(chanel_name='Rates')
            sub=Subdivision.objects.create(subdivision_key='A',subdivision_name='A',subdivision_global='A',chanel=channel)
            first=SubdivisionCoeff.objects.create(version=self.first,subdivision=sub,goods=original.goods,type_coeff=original.type_coeff,segment=original.segment,variation_calculate=original.variation_calculate,motivation_coeff=.003,manager_coeff=1)
            other=Goods.objects.create(goods_key='Other',planning_group_sales='Other',group='Other',brand='Other')
            SubdivisionCoeff.objects.create(version=self.first,subdivision=sub,goods=other,type_coeff=original.type_coeff,segment=original.segment,variation_calculate=original.variation_calculate,motivation_coeff=.007,manager_coeff=1)
            return sub.pk,original.goods_id,first.pk,other.pk
        sub,good,first_id,other=self.db(fixture)
        self.page.goto(self.live_server_url+reverse('coeff_subdivisions_admin_motivation',args=[sub]))
        first=self.page.locator(f'[name="sub_coeff={first_id}"]')
        expect(first.locator('..')).to_contain_text('Глобально: 0,2%')
        self.page.get_by_role('button',name='Изменить ставки',exact=True).click()
        self.page.locator(f'.bulk-good[value="{good}"]').check()
        self.page.locator('#coefficient-search').fill('Other')
        self.page.locator('#multiply-filtered').click()
        expect(self.page.locator('.bulk-good:checked')).to_have_count(2)
        self.page.locator('#multiply-operation').select_option('global')
        self.page.locator('#multiply-preview').click()
        expect(self.page.locator('#multiply-error')).to_contain_text('нет точной глобальной ставки')
        expect(first).to_have_value('0.3%')
        self.page.locator(f'.bulk-good[value="{other}"]').uncheck()
        self.page.locator('#multiply-preview').click()
        expect(self.page.locator('#multiply-review-summary')).to_contain_text('скрыто сейчас: 1')
        self.page.locator('#multiply-apply').click()
        expect(first).to_have_value('0.2%')
        self.assertEqual(self.db(lambda:SubdivisionCoeff.objects.get(pk=first_id).motivation_coeff),.003)
        self.page.locator('#coefficient-reset').click()
        self.page.locator('#editor-undo').click();expect(first).to_have_value('0.3%')
        expect(first.locator('..')).to_contain_text('Своя ставка')
        self.assertEqual(self.errors,[])
