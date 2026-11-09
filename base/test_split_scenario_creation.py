from datetime import date
from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from .models import SplitScenario
from .split_forms import ScenarioForm
from .split_engine import planning_months, forecast_months, evaluate, quantity


class ScenarioDatesFormTests(SimpleTestCase):
    def values(self, **changes):
        return {'title': 'План', 'start': '2027-11-15', 'end': '2028-02-10', 'stock_date': '2027-10-31', **changes}

    def test_creation_has_exactly_four_required_fields_and_does_not_need_directories(self):
        form = ScenarioForm(self.values())
        self.assertEqual(set(form.fields), {'title', 'start', 'end', 'stock_date'})
        self.assertTrue(all(field.required for field in form.fields.values()))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['end'], date(2028, 2, 10))
        for field in form.fields:
            values = self.values()
            values.pop(field)
            self.assertFalse(ScenarioForm(values).is_valid())

    def test_date_order_and_stock_snapshot_on_start_day(self):
        for changes in ({'end': '2027-11-14'}, {'stock_date': '2027-11-16'}):
            form = ScenarioForm(self.values(**changes))
            self.assertFalse(form.is_valid())
        form = ScenarioForm(self.values(end='2027-11-15', stock_date='2027-11-15'))
        self.assertTrue(form.is_valid(), form.errors)


class EmptyScenarioTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('empty-scenario-admin'))

    def test_empty_cross_year_scenario_opens_all_tabs_and_defers_composition(self):
        response = self.client.post(reverse('split_scenarios'), {'title': 'Даты без состава',
            'start': '2027-11-15', 'end': '2028-02-10', 'stock_date': '2027-10-31',
            'groups': ['999'], 'channels': ['888'], 'entry_mode': 'annual', 'year': '2050'})
        self.assertEqual(response.status_code, 302)
        scenario = SplitScenario.objects.get()
        self.assertEqual((scenario.start, scenario.end, scenario.stock_date), (date(2027,11,15), date(2028,2,10), date(2027,10,31)))
        self.assertEqual(scenario.year, 2027)
        self.assertEqual(scenario.entry_mode, 'period')
        self.assertEqual(scenario.state['groups'], {})
        self.assertEqual(scenario.state['channels'], {})
        self.assertEqual(planning_months(scenario), ['2027-11', '2027-12', '2028-01', '2028-02'])
        self.assertEqual(forecast_months(scenario), ['2027-11'])
        self.assertTrue(evaluate(scenario)['errors'])
        self.assertIsNone(evaluate(scenario)['totals']['closing'])
        url = reverse('split_scenario', args=[scenario.pk])
        for tab in ('plan', 'distribution', 'stock', 'sources', 'checks', 'versions', 'detail'):
            page = self.client.get(url, {'tab': tab})
            self.assertEqual(page.status_code, 200, tab)
        page = self.client.get(url)
        self.assertContains(page, 'Начать планирование')
        self.assertContains(page, 'name="groups" multiple')
        self.assertContains(page, 'name="volume_mode"')
        creation = self.client.get(reverse('split_scenarios'))
        for field in ('year', 'mode', 'entry_mode', 'groups', 'channels'):
            self.assertNotContains(creation, 'name="' + field + '"')
        for field in ('start', 'end', 'stock_date'):
            self.assertContains(creation, 'name="' + field + '"')

    def test_empty_composition_and_approval_are_blocked_without_changing_draft(self):
        self.client.post(reverse('split_scenarios'), {'title': 'Пустой', 'start':'2027-01-01', 'end':'2027-06-30', 'stock_date':'2026-12-31'})
        scenario = SplitScenario.objects.get()
        url = reverse('split_scenario', args=[scenario.pk])
        self.client.post(url, {'action':'composition', 'confirm':'on', 'revision':1})
        self.client.post(url, {'action':'approve', 'revision':1})
        scenario.refresh_from_db()
        self.assertEqual(scenario.revision, 1)
        self.assertEqual(scenario.status, 'draft')
