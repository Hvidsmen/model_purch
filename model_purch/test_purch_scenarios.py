from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse

from .models import ScenarioModel, Purch, PurchPay, KindLagPay


class PurchScenarioTests(TestCase):
    def setUp(self):
        self.source = ScenarioModel.objects.create(name='Source', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.target = ScenarioModel.objects.create(name='Target', date_start_plan='2026-02-01', date_end_plan='2026-12-31')
        self.latest = ScenarioModel.objects.create(name='Latest', date_start_plan='2026-03-01', date_end_plan='2026-12-31')
        self.purchase = Purch.objects.create(name='Source supplier', scenario_plan=self.source, lag_income=90, lage_make=35)
        self.kind = KindLagPay.objects.create(name='Delivery')
        PurchPay.objects.create(purch=self.purchase, name='Payment', percent_pay=100, lag_day_pay=10, kind_lag_pay=self.kind)
        self.target_purchase = Purch.objects.create(name='Target supplier', scenario_plan=self.target, lag_income=10)

    def test_selection_filters_list_and_preserves_action_targets(self):
        response = self.client.get(reverse('purch_list'), {'scenario': self.target.pk})
        self.assertContains(response, 'id="purch-scenario"')
        self.assertContains(response, f'value="{self.target.pk}" selected')
        self.assertContains(response, 'Target supplier')
        self.assertNotContains(response, 'Source supplier')
        self.assertContains(response, f'{reverse("purch_create")}?scenario={self.target.pk}')
        self.assertContains(response, f'name="scenario" value="{self.target.pk}"')

    def test_copy_uses_posted_target_and_preserves_all_purchase_settings(self):
        url = reverse('copy_purch_from_scenario') + f'?scenario={self.latest.pk}'
        data = {'scenario': self.target.pk, 'source_scenario_id': self.source.pk}
        for _ in range(2):
            response = self.client.post(url, data)
            self.assertRedirects(response, reverse('purch_list') + f'?scenario={self.target.pk}')
        purchase = Purch.objects.get(scenario_plan=self.target, name=self.purchase.name)
        self.assertEqual((purchase.lag_income, purchase.lage_make), (90, 35))
        payment = purchase.purchpay_set.get()
        self.assertEqual((payment.percent_pay, payment.lag_day_pay, payment.kind_lag_pay_id), (100, 10, self.kind.pk))
        self.assertFalse(Purch.objects.filter(scenario_plan=self.latest).exists())
        self.assertEqual(Purch.objects.filter(scenario_plan=self.source).count(), 1)

    def test_invalid_copy_does_not_fall_back_to_latest(self):
        for target in ['', 'invalid', 99999, self.source.pk]:
            self.client.post(reverse('copy_purch_from_scenario'), {'scenario': target, 'source_scenario_id': self.source.pk})
        self.assertEqual(Purch.objects.count(), 2)

    def test_delete_returns_to_selected_scenario(self):
        response = self.client.post(reverse('purch_delete', args=[self.target_purchase.pk]) + f'?scenario={self.target.pk}')
        self.assertRedirects(response, reverse('purch_list') + f'?scenario={self.target.pk}')
        self.assertTrue(Purch.objects.filter(pk=self.purchase.pk).exists())

    def test_copy_form_button_enabled_and_source_required(self):
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = []

            def handle_starttag(self, tag, attrs):
                self.elements.append((tag, dict(attrs)))

        response = self.client.get(reverse('purch_list'), {'scenario': self.target.pk})
        parser = Parser()
        parser.feed(response.content.decode())
        button = next(attrs for tag, attrs in parser.elements
                      if tag == 'button' and attrs.get('id') == 'confirmCopyPurchBtn')
        self.assertEqual(button['type'], 'submit')
        self.assertNotIn('disabled', button)
        source = next(attrs for tag, attrs in parser.elements
                      if tag == 'select' and attrs.get('name') == 'source_scenario_id')
        self.assertIn('required', source)
        self.assertEqual(source['id'], 'copy-purch-source')
        response = self.client.post(reverse('copy_purch_from_scenario'), {
            'scenario': self.target.pk, 'source_scenario_id': self.source.pk,
        })
        self.assertRedirects(response, reverse('purch_list') + f'?scenario={self.target.pk}')
        self.assertTrue(Purch.objects.filter(scenario_plan=self.target, name=self.purchase.name).exists())
