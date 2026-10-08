from base.testing import authorize_test_case, authorize_admin
from .services.approval import review_approval, approve_review
from datetime import date
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, Client
from django.urls import reverse
from .models import GlobalCoeffVersion, GlobalCoeff, Goods, SegmentCoeff, TypeCoeff, VariationCalculate, Chanel, Subdivision, SubdivisionCoeff
from .services.versions import create_version, effective_version, apply_to_subdivisions


class GlobalCoefficientVersionTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.first = GlobalCoeffVersion.objects.get(effective_from=date(2001, 1, 1))
        self.good = Goods.objects.create(goods_key='Key', planning_group_sales='Sales', group='Group', brand='Brand')
        self.segment = SegmentCoeff.objects.create(segment_name='Segment')
        self.kind = TypeCoeff.objects.create(type_coeff_name='Политики')
        self.sales = TypeCoeff.objects.create(type_coeff_name='Продажи')
        self.variation = VariationCalculate.objects.create(variation_name='Level')
        self.coeff = GlobalCoeff.objects.create(version=self.first, goods=self.good, type_coeff=self.kind,
            segment=self.segment, motivation_coeff=0.123456, manager_coeff=1, variation_calculate=self.variation)

    def create(self, starts='2026-01-01'):
        return create_version(date.fromisoformat(starts), 'New', self.first.pk)

    def post(self, version, action, **extra):
        return self.client.post(reverse('gb_act'), {'version': version.pk, 'action_button': action, **extra})

    def test_dates_required_and_strictly_increasing(self):
        count = GlobalCoeffVersion.objects.count()
        for starts in ['', '2000-01-01', '2001-01-01', 'bad']:
            response = self.client.post(reverse('gb_act'), {'action_button': 'create_version', 'effective_from': starts, 'source_version': self.first.pk})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(GlobalCoeffVersion.objects.count(), count)
            self.assertTrue(response.context['version_form'].errors)
        latest = self.create()
        with self.assertRaises(ValidationError):
            create_version(date(2025, 1, 1), source_version=latest.pk)
        with self.assertRaises(ValidationError):
            create_version(date(2026, 1, 1), source_version=latest.pk)
        with self.assertRaises(ValidationError):
            create_version(date(2027, 1, 1), source_version=self.first.pk)
        latest.effective_from = date(2028, 1, 1)
        with self.assertRaises(ValidationError):
            latest.save()

    def test_new_version_copies_values_and_freezes_previous_version(self):
        latest = self.create()
        copied = latest.coefficients.get()
        self.assertNotEqual(copied.pk, self.coeff.pk)
        self.assertEqual(copied.motivation_coeff, 0.123456)
        self.post(latest, 'save_coeff', **{f'select_var_calc={self.good.pk}': self.variation.pk, f'global_coeff={copied.pk}': '2.5%'})
        self.coeff.refresh_from_db()
        copied.refresh_from_db()
        self.assertEqual(self.coeff.motivation_coeff, 0.123456)
        self.assertEqual(copied.motivation_coeff, 0.025)
        response = self.post(self.first, 'save_coeff', **{f'select_var_calc={self.good.pk}': self.variation.pk, f'global_coeff={self.coeff.pk}': '50%'})
        self.assertEqual(response.status_code, 302)
        self.coeff.refresh_from_db()
        self.assertEqual(self.coeff.motivation_coeff, 0.123456)
        with self.assertRaises(ValidationError):
            self.coeff.save()
        with self.assertRaises(ValidationError):
            self.coeff.delete()
        with self.assertRaises(ValidationError):
            self.first.coefficients.update(motivation_coeff=0.8)
        with self.assertRaises(ValidationError):
            self.first.coefficients.all().delete()

    def test_delete_affects_only_selected_latest_version_and_keeps_goods(self):
        latest = self.create()
        self.post(latest, 'save_coeff', **{f'delete_coeff={self.good.pk}': 'on'})
        self.assertFalse(latest.coefficients.exists())
        self.assertTrue(self.first.coefficients.exists())
        self.assertTrue(Goods.objects.filter(pk=self.good.pk).exists())

    def test_partial_post_does_not_delete_other_cells_and_invalid_numbers_roll_back(self):
        second = GlobalCoeff.objects.create(version=self.first, goods=self.good, type_coeff=self.sales,
            segment=self.segment, motivation_coeff=0.1, manager_coeff=1, variation_calculate=self.variation)
        self.post(self.first, 'save_coeff', **{f'select_var_calc={self.good.pk}': self.variation.pk, f'global_coeff={self.coeff.pk}': '0.2'})
        second.refresh_from_db()
        self.assertEqual(second.motivation_coeff, 0.1)
        self.post(self.first, 'save_coeff', **{f'select_var_calc={self.good.pk}': self.variation.pk,
            f'global_coeff={self.coeff.pk}': '0.3', f'global_coeff={second.pk}': 'nan'})
        self.coeff.refresh_from_db()
        self.assertEqual(self.coeff.motivation_coeff, 0.2)

    def test_effective_version_and_future_application(self):
        channel = Chanel.objects.create(chanel_name='Channel')
        sub = Subdivision.objects.create(subdivision_key='Key', subdivision_name='Name', subdivision_global='Name', chanel=channel)
        apply_to_subdivisions(self.first, [sub])
        approve_review(review_approval(self.first)['token'])
        future = self.create('2099-01-01')
        apply_to_subdivisions(future, [sub])
        approve_review(review_approval(future)['token'])
        self.assertEqual(effective_version(date(2001, 1, 1)), self.first)
        self.assertIsNone(effective_version(date(2000, 12, 31)))
        self.assertEqual(effective_version(date(2098, 12, 31)), self.first)
        self.assertEqual(effective_version(date(2099, 1, 1)), future)
        self.assertEqual(SubdivisionCoeff.objects.filter(version=self.first).get().motivation_coeff, 0.123456)
        self.assertEqual(SubdivisionCoeff.objects.filter(version=future).get().motivation_coeff, 0.123456)
        with self.assertRaises(ValidationError):
            apply_to_subdivisions(self.first, [sub])

    def test_page_reads_history_without_sql_or_creating_coefficients(self):
        latest = self.create()
        with patch('admin_motivation.models.create_engine') as sql:
            response = self.client.get(reverse('global_coeff_admin_motivation'), {'version': self.first.pk})
            sql.assert_not_called()
        self.assertContains(response, '01.01.2001')
        self.assertContains(response, 'Не утверждена')
        self.assertContains(response, 'Историческая версия: только просмотр')
        self.assertFalse(response.context['version_editable'])
        self.assertEqual(GlobalCoeff.objects.count(), 2)
        self.assertEqual(self.client.get(reverse('global_coeff_admin_motivation')).context['selected_version'], self.first)

    def test_create_and_edit_product_are_scoped_to_latest_version(self):
        latest = self.create()
        self.post(latest, 'add_product', pg_sales='Sales', group='Group', brand='Brand',
                  select_var_calc=self.variation.pk, k0='0.4', s0='0.5')
        self.assertEqual(latest.coefficients.get(type_coeff=self.kind).motivation_coeff, 0.4)
        self.assertEqual(latest.coefficients.get(type_coeff=self.sales).motivation_coeff, 0.5)
        self.coeff.refresh_from_db()
        self.assertEqual(self.coeff.motivation_coeff, 0.123456)

    def test_post_requires_csrf_and_get_cannot_mutate(self):
        client = Client(enforce_csrf_checks=True)
        authorize_admin(client)
        self.assertEqual(client.post(reverse('gb_act'), {'action_button': 'create_version'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('gb_act')).status_code, 405)


class GlobalCoefficientMigrationTests(TransactionTestCase):
    def test_existing_coefficients_preserved_in_first_version(self):
        old = [('admin_motivation', '0005_examplefiles')]
        new = [('admin_motivation', '0006_globalcoeffversion_alter_globalcoeff_goods_and_more')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        Good = apps.get_model('admin_motivation', 'Goods')
        Coeff = apps.get_model('admin_motivation', 'GlobalCoeff')
        good = Good.objects.create(goods_key='Goods', planning_group_sales='Sales', group='Group', brand='Brand')
        kind = apps.get_model('admin_motivation', 'TypeCoeff').objects.create(type_coeff_name='Kind')
        segment = apps.get_model('admin_motivation', 'SegmentCoeff').objects.create(segment_name='Segment')
        variation = apps.get_model('admin_motivation', 'VariationCalculate').objects.create(variation_name='Level')
        coeff = Coeff.objects.create(goods=good, type_coeff=kind, segment=segment, variation_calculate=variation, motivation_coeff=0.123456, manager_coeff=0.75)
        before = Coeff.objects.values().get(pk=coeff.pk)
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        apps = executor.loader.project_state(new).apps
        version = apps.get_model('admin_motivation', 'GlobalCoeffVersion').objects.get()
        self.assertEqual(version.effective_from, date(2001, 1, 1))
        after = apps.get_model('admin_motivation', 'GlobalCoeff').objects.values().get(pk=coeff.pk)
        self.assertEqual(after.pop('version_id'), version.pk)
        self.assertEqual(after, before)
