from base.testing import authorize_test_case
from html.parser import HTMLParser

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .models import KindPurch, PGGoods, ScenarioModel


class CopyGoodsTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.source = ScenarioModel.objects.create(name='Source', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        self.target = ScenarioModel.objects.create(name='Target', date_start_plan='2025-01-01', date_end_plan='2025-12-31')
        self.other = ScenarioModel.objects.create(name='Other', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.kind = KindPurch.objects.create(name='Purchased')
        self.source_goods = self.make_goods(self.source)
        self.url = reverse('copy_pggoods_from_scenario')

    def make_goods(self, scenario, **overrides):
        fields = dict(
            scenario_plan=scenario, planning_group='PG', planning_sales='Sales', group_goods='Group',
            brand='Brand', purch='Supplier', kind_purch=self.kind,
            volume=1.5, exw_usd=10, ddp_usd=15, kddp=1.5, stock_cnt_day=30, percent_stock_end=20,
        )
        fields.update(overrides)
        return PGGoods.objects.create(**fields)

    def copy(self, **overrides):
        data = {'scenario': self.target.pk, 'source_scenario_id': self.source.pk}
        data.update(overrides)
        return self.client.post(self.url, data)

    def assert_same_goods_values(self, copy):
        self.source_goods.refresh_from_db()
        for field in PGGoods._meta.concrete_fields:
            if field.name not in {'id', 'scenario_plan'}:
                self.assertEqual(getattr(copy, field.attname), getattr(self.source_goods, field.attname), field.name)

    def test_selected_post_target_is_used_instead_of_latest_scenario(self):
        original_source = dict(PGGoods.objects.filter(pk=self.source_goods.pk).values().get())
        response = self.copy()
        self.assertRedirects(response, f'{reverse("pggoods_list")}?scenario={self.target.pk}', fetch_redirect_response=False)
        copy = PGGoods.objects.get(scenario_plan=self.target)
        self.assert_same_goods_values(copy)
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.other).count(), 0)
        self.assertEqual(PGGoods.objects.filter(pk=self.source_goods.pk).values().get(), original_source)

    def test_post_target_takes_precedence_over_conflicting_query_string(self):
        response = self.client.post(f'{self.url}?scenario={self.other.pk}', {
            'scenario': self.target.pk, 'source_scenario_id': self.source.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.target).count(), 1)
        self.assertFalse(PGGoods.objects.filter(scenario_plan=self.other).exists())

    def test_existing_goods_updated_and_repeated_copy_does_not_duplicate(self):
        target_goods = self.make_goods(self.target, brand='Old', purch='Old supplier', stock_cnt_day=1, exw_usd=2)
        unrelated = self.make_goods(self.target, planning_group='Unrelated')
        self.make_goods(self.source, planning_group='PG2')
        self.copy()
        self.copy()
        target_goods.refresh_from_db()
        self.assert_same_goods_values(target_goods)
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.target).count(), 3)
        self.assertTrue(PGGoods.objects.filter(pk=unrelated.pk).exists())

    def test_invalid_or_missing_scenarios_do_not_copy_to_default_scenario(self):
        for name in ['scenario', 'source_scenario_id']:
            for invalid in ['', 'not-an-id', '999999']:
                with self.subTest(field=name, value=invalid):
                    response = self.copy(**{name: invalid})
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(list(get_messages(response.wsgi_request)))
                    self.assertEqual(PGGoods.objects.count(), 1)

    def test_copy_to_same_scenario_does_not_change_goods(self):
        response = self.copy(scenario=self.source.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PGGoods.objects.count(), 1)
        self.assertTrue(list(get_messages(response.wsgi_request)))

    def test_copy_form_can_be_submitted_after_selecting_source(self):
        class FormParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = []

            def handle_starttag(self, tag, attrs):
                self.elements.append((tag, dict(attrs)))

        response = self.client.get(f'{reverse("pggoods_list")}?scenario={self.target.pk}')
        self.assertEqual(response.status_code, 200)
        parser = FormParser()
        parser.feed(response.content.decode())
        button = next(attrs for tag, attrs in parser.elements if attrs.get('id') == 'confirmCopyPggoodsBtn')
        self.assertNotIn('disabled', button)
        self.assertTrue(any(tag == 'input' and attrs.get('name') == 'scenario' and attrs.get('value') == str(self.target.pk)
                            for tag, attrs in parser.elements))
        select = next(attrs for tag, attrs in parser.elements if tag == 'select' and attrs.get('name') == 'source_scenario_id')
        self.assertIn('required', select)
