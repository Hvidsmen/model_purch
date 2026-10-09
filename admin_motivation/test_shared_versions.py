from base.testing import authorize_test_case
from .services.approval import review_approval, approve_review
from datetime import date
from io import BytesIO
from unittest.mock import Mock, patch
import pandas as pd
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from .models import (GlobalCoeffVersion, GlobalCoeff, SubdivisionCoeff, SubdivisionManagerCoeff,
                     Goods, SegmentCoeff, TypeCoeff, VariationCalculate, Subdivision, Chanel, KindManagerCoeff)
from .services.versions import create_version, apply_to_subdivisions
from .services.sql_export import export_motivation


class SharedMotivationVersionTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.first = GlobalCoeffVersion.objects.get(effective_from=date(2001, 1, 1))
        channel = Chanel.objects.create(chanel_name='Channel')
        self.sub = Subdivision.objects.create(subdivision_key='A', subdivision_name='A', subdivision_global='A', chanel=channel)
        self.other = Subdivision.objects.create(subdivision_key='B', subdivision_name='B', subdivision_global='B', chanel=channel)
        self.good = Goods.objects.create(goods_key='Key', planning_group_sales='Sales', group='Group', brand='Brand')
        self.segment = SegmentCoeff.objects.create(segment_name='Segment')
        self.kind = TypeCoeff.objects.create(type_coeff_name='Политики')
        TypeCoeff.objects.create(type_coeff_name='Продажи')
        self.variation = VariationCalculate.objects.create(variation_name='Level')
        self.manager_kind = KindManagerCoeff.objects.create(name='Year')
        values = dict(version=self.first, goods=self.good, type_coeff=self.kind, segment=self.segment,
                      variation_calculate=self.variation, motivation_coeff=0.1, manager_coeff=1)
        self.global_coeff = GlobalCoeff.objects.create(**values)
        self.local_coeff = SubdivisionCoeff.objects.create(subdivision=self.sub, **{**values, 'motivation_coeff': 0.15})
        SubdivisionCoeff.objects.create(subdivision=self.other, **{**values, 'motivation_coeff': 0.3})
        self.manager = SubdivisionManagerCoeff.objects.create(version=self.first, subdivision=self.sub, kind=self.manager_kind, coeff=0.8)

    def create(self, starts='2026-10-08', source=None):
        return create_version(date.fromisoformat(starts), 'Shared', (source or self.first).pk)

    def post(self, version, action, **data):
        return self.client.post(reverse('sub_act', args=[self.sub.pk]), {'version': version.pk, 'action_button': action, **data})

    def test_one_creation_clones_global_subdivisions_and_managers(self):
        latest = self.create()
        self.assertEqual(latest.coefficients.get().motivation_coeff, 0.1)
        self.assertEqual(latest.subdivision_coefficients.get(subdivision=self.sub).motivation_coeff, 0.15)
        self.assertEqual(latest.subdivision_coefficients.get(subdivision=self.other).motivation_coeff, 0.3)
        self.assertEqual(latest.manager_coefficients.get().coeff, 0.8)
        self.assertEqual(GlobalCoeffVersion.objects.count(), 2)
        self.assertNotEqual(latest.manager_coefficients.get().pk, self.manager.pk)

    def test_create_version_from_subdivision_returns_to_same_page(self):
        response = self.client.post(reverse('gb_act'), {'action_button': 'create_version',
            'effective_from': '2026-10-08', 'source_version': self.first.pk, 'return_subdivision': self.sub.pk})
        latest = GlobalCoeffVersion.objects.order_by('-effective_from').first()
        self.assertRedirects(response, reverse('coeff_subdivisions_admin_motivation', args=[self.sub.pk]) + f'?version={latest.pk}')
        self.assertEqual(latest.subdivision_coefficients.count(), 2)

    def test_edit_is_scoped_to_version_and_subdivision(self):
        latest = self.create()
        target = latest.subdivision_coefficients.get(subdivision=self.sub)
        foreign = latest.subdivision_coefficients.get(subdivision=self.other)
        self.post(latest, 'save_coeff', **{f'sub_coeff={target.pk}': '25%', f'sub_coeff={foreign.pk}': '99%',
            f'sub_coeff={self.local_coeff.pk}': '99%', f'select_var_calc={self.good.pk}': self.variation.pk})
        self.post(latest, 'update_motive_coeff', **{f'kind_coeff={self.manager_kind.pk}': '0.9'})
        self.assertEqual(latest.subdivision_coefficients.get(subdivision=self.sub).motivation_coeff, 0.25)
        self.assertEqual(latest.subdivision_coefficients.get(subdivision=self.other).motivation_coeff, 0.3)
        self.assertEqual(latest.manager_coefficients.get().coeff, 0.9)
        self.local_coeff.refresh_from_db()
        self.manager.refresh_from_db()
        self.assertEqual(self.local_coeff.motivation_coeff, 0.15)
        self.assertEqual(self.manager.coeff, 0.8)

    def test_history_blocks_all_actions_and_direct_mutations(self):
        self.create()
        for action in ['save_coeff', 'update_motive_coeff', 'copy_from_global', 'file_load']:
            response = self.post(self.first, action, **{f'sub_coeff={self.local_coeff.pk}': '0.9', f'kind_coeff={self.manager_kind.pk}': '0.9'})
            self.assertEqual(response.status_code, 302)
        for rows in [self.first.subdivision_coefficients.all(), self.first.manager_coefficients.all()]:
            with self.assertRaises(ValidationError):
                rows.delete()
        self.manager.coeff = 0.9
        with self.assertRaises(ValidationError):
            self.manager.save()
        self.local_coeff.refresh_from_db()
        self.assertEqual(self.local_coeff.motivation_coeff, 0.15)

    def test_copy_uses_globals_from_same_future_version_and_preserves_other_data(self):
        latest = self.create('2099-01-01')
        coefficient = latest.coefficients.get()
        coefficient.motivation_coeff = 0.6
        coefficient.save()
        self.post(latest, 'copy_from_global')
        self.assertEqual(latest.subdivision_coefficients.get(subdivision=self.sub).motivation_coeff, 0.6)
        self.assertEqual(latest.subdivision_coefficients.get(subdivision=self.other).motivation_coeff, 0.3)
        self.assertEqual(latest.manager_coefficients.get().coeff, 0.8)
        self.local_coeff.refresh_from_db()
        self.assertEqual(self.local_coeff.motivation_coeff, 0.15)

    def test_selection_is_shared_between_pages_and_gets_do_not_write_or_connect(self):
        latest = self.create()
        self.client.get(reverse('global_coeff_admin_motivation'), {'version': self.first.pk})
        with patch('admin_motivation.models.create_engine') as sql:
            response = self.client.get(reverse('coeff_subdivisions_admin_motivation', args=[self.sub.pk]))
            sql.assert_not_called()
        self.assertEqual(response.context['selected_version'], self.first)
        self.assertFalse(response.context['version_editable'])
        self.client.get(reverse('coeff_subdivisions_admin_motivation', args=[self.sub.pk]), {'version': latest.pk})
        self.assertEqual(self.client.get(reverse('global_coeff_admin_motivation')).context['selected_version'], latest)
        self.assertEqual(SubdivisionManagerCoeff.objects.count(), 2)
        self.assertEqual(SubdivisionCoeff.objects.count(), 4)

    def test_invalid_individual_values_rollback_and_get_cannot_write(self):
        self.assertEqual(self.client.get(reverse('sub_act', args=[self.sub.pk])).status_code, 405)
        self.post(self.first, 'save_coeff', **{f'sub_coeff={self.local_coeff.pk}': 'nan'})
        self.local_coeff.refresh_from_db()
        self.assertEqual(self.local_coeff.motivation_coeff, 0.15)

    def test_excel_updates_only_target_subdivision_in_latest_version(self):
        latest = self.create()
        stream = BytesIO()
        frame = pd.DataFrame([[1, 'Sales', 'Group', 'Brand', 0.4, 0.5]], columns=['ID', 'Sales', 'Group', 'Brand', 'K0', 'S0'])
        frame.to_excel(stream, index=False, startrow=1)
        upload = SimpleUploadedFile('coeff.xlsx', stream.getvalue())
        self.post(latest, 'file_load', myfile=upload)
        self.assertEqual(latest.subdivision_coefficients.get(subdivision=self.sub, type_coeff=self.kind).motivation_coeff, 0.4)
        self.assertEqual(latest.subdivision_coefficients.get(subdivision=self.other).motivation_coeff, 0.3)
        self.assertEqual(latest.coefficients.get().motivation_coeff, 0.1)
        self.local_coeff.refresh_from_db()
        self.assertEqual(self.local_coeff.motivation_coeff, 0.15)

    def connector(self):
        connection, cursor = Mock(), Mock()
        return Mock(return_value=(connection, cursor)), connection, cursor

    def test_sql_exports_full_timeline_with_exact_start_dates_and_matching_versions(self):
        approve_review(review_approval(self.first)['token'])
        latest = self.create()
        latest.subdivision_coefficients.filter(subdivision=self.sub).update(motivation_coeff=0.4)
        latest.manager_coefficients.update(coeff=0.9)
        approve_review(review_approval(latest)['token'])
        connector, connection, cursor = self.connector()
        exported = export_motivation(connector)
        self.assertEqual([v.pk for v in exported], [self.first.pk, latest.pk])
        batches = cursor.executemany.call_args_list
        coefficients = [call.args[1] for call in batches if 'SubdivisionMotiveCoeff' in call.args[0]]
        managers = [call.args[1] for call in batches if 'SubdivisionManagerCoeff' in call.args[0]]
        self.assertEqual(coefficients[0][0][0], self.first.effective_from)
        self.assertEqual(coefficients[0][0][-1], 0.15)
        self.assertEqual(coefficients[1][0][0], date(2026, 10, 8))
        self.assertEqual(coefficients[1][0][-1], 0.4)
        self.assertEqual(managers[0][0][-1], 0.8)
        self.assertEqual(managers[1][0][-1], 0.9)
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        connection.close.assert_called_once()
        self.assertEqual(cursor.execute.call_count, 3)
        self.assertTrue(all(call.args[1] == self.first.effective_from for call in cursor.execute.call_args_list))

    def test_failed_sql_export_rolls_back_and_closes_connection(self):
        approve_review(review_approval(self.first)['token'])
        connector, connection, cursor = self.connector()
        cursor.executemany.side_effect = RuntimeError('Failed insert')
        with self.assertRaises(RuntimeError):
            export_motivation(connector)
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    def test_export_without_approved_versions_blocks_connection(self):
        connector, _, _ = self.connector()
        with self.assertRaises(ValidationError):
            export_motivation(connector)
        connector.assert_not_called()


class SharedSubdivisionMigrationTests(TransactionTestCase):
    def test_preserves_original_rows_and_populates_already_existing_versions(self):
        old = [('admin_motivation', '0006_globalcoeffversion_alter_globalcoeff_goods_and_more')]
        new = [('admin_motivation', '0007_alter_globalcoeffversion_options_and_more')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        Version = apps.get_model('admin_motivation', 'GlobalCoeffVersion')
        first, _ = Version.objects.get_or_create(effective_from=date(2001, 1, 1))
        later = Version.objects.create(effective_from=date(2026, 10, 8))
        channel = apps.get_model('admin_motivation', 'Chanel').objects.create(chanel_name='Channel')
        sub = apps.get_model('admin_motivation', 'Subdivision').objects.create(subdivision_key='Key', subdivision_name='Name', subdivision_global='Name', chanel=channel)
        good = apps.get_model('admin_motivation', 'Goods').objects.create(goods_key='Key', planning_group_sales='Sales', group='Group', brand='Brand')
        segment = apps.get_model('admin_motivation', 'SegmentCoeff').objects.create(segment_name='Segment')
        kind = apps.get_model('admin_motivation', 'TypeCoeff').objects.create(type_coeff_name='Kind')
        variation = apps.get_model('admin_motivation', 'VariationCalculate').objects.create(variation_name='Level')
        manager_kind = apps.get_model('admin_motivation', 'KindManagerCoeff').objects.create(name='Year')
        apps.get_model('admin_motivation', 'SubdivisionCoeff').objects.create(subdivision=sub, goods=good, type_coeff=kind,
            segment=segment, variation_calculate=variation, motivation_coeff=0.42, manager_coeff=1)
        apps.get_model('admin_motivation', 'SubdivisionManagerCoeff').objects.create(subdivision=sub, kind=manager_kind, coeff=0.8)
        originals = {name: list(apps.get_model('admin_motivation', name).objects.values()) for name in ['SubdivisionCoeff', 'SubdivisionManagerCoeff']}
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        apps = executor.loader.project_state(new).apps
        for name, original in originals.items():
            Model = apps.get_model('admin_motivation', name)
            self.assertEqual(Model.objects.count(), 2)
            baseline = list(Model.objects.filter(version_id=first.pk).values())
            for row in baseline:
                row.pop('version_id')
            self.assertEqual(baseline, original)
            self.assertEqual(Model.objects.filter(version_id=later.pk).count(), 1)
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        for name, original in originals.items():
            self.assertEqual(list(apps.get_model('admin_motivation', name).objects.values()), original)
