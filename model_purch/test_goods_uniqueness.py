from base.testing import authorize_test_case
from io import BytesIO, StringIO
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from .goods_identity import planning_group_key
from .models import KindPurch, PGGoods, ScenarioModel
from .views import sync_pggoods_data_for_scenario


class GoodsUniquenessTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.scenario = ScenarioModel.objects.create(name='Plan', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.other = ScenarioModel.objects.create(name='Other', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        self.scenario.refresh_from_db()
        self.other.refresh_from_db()
        self.kind = KindPurch.objects.create(name='Purchased')

    def fields(self, **changes):
        data = dict(scenario_plan=self.scenario, planning_group='Daikin SENSIRA', planning_sales='Sales',
                    group_goods='Group', brand='Brand', purch='Supplier', kind_purch=self.kind,
                    volume=1, exw_usd=2, ddp_usd=3, kddp=1.5, stock_cnt_day=30, percent_stock_end=20)
        data.update(changes)
        return data

    def test_database_rejects_case_variants_even_with_different_metadata(self):
        PGGoods.objects.create(**self.fields())
        with self.assertRaises(IntegrityError), transaction.atomic():
            PGGoods.objects.create(**self.fields(planning_group=' daikin Sensira ', planning_sales='Different', group_goods='Other'))
        self.assertEqual(PGGoods.objects.count(), 1)

    def test_separate_scenarios_can_have_same_group(self):
        PGGoods.objects.create(**self.fields())
        PGGoods.objects.create(**self.fields(scenario_plan=self.other, planning_group='daikin sensira'))
        self.assertEqual(PGGoods.objects.count(), 2)

    def test_cyrillic_case_and_canonical_unicode_share_identity(self):
        self.assertEqual(planning_group_key(' ВЕНТ Канальная '), planning_group_key('вент канальная'))
        self.assertEqual(planning_group_key('Cafe\u0301'), planning_group_key('Café'))

    def test_model_validation_rejects_duplicate_for_admin(self):
        PGGoods.objects.create(**self.fields())
        with self.assertRaises(ValidationError):
            PGGoods(**self.fields(planning_group='daikin sensira')).full_clean()

    def test_bulk_create_cannot_bypass_uniqueness(self):
        PGGoods.objects.create(**self.fields())
        with self.assertRaises(IntegrityError), transaction.atomic():
            PGGoods.objects.bulk_create([PGGoods(**self.fields(planning_group='DAIKIN SENSIRA'))])

    def test_queryset_rename_cannot_bypass_uniqueness(self):
        PGGoods.objects.create(**self.fields())
        other = PGGoods.objects.create(**self.fields(planning_group='Another'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            PGGoods.objects.filter(pk=other.pk).update(planning_group='daikin sensira')
        other.refresh_from_db()
        self.assertEqual(other.planning_group, 'Another')

    def test_copy_updates_matching_group_despite_classification_differences(self):
        source = PGGoods.objects.create(**self.fields(scenario_plan=self.other, planning_group='DAIKIN SENSIRA',
                                                     planning_sales='New sales', group_goods='New group', ddp_usd=55))
        target = PGGoods.objects.create(**self.fields())
        response = self.client.post(reverse('copy_pggoods_from_scenario'),
                                    {'scenario': self.scenario.pk, 'source_scenario_id': self.other.pk})
        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertEqual(target.planning_sales, source.planning_sales)
        self.assertEqual(target.group_goods, source.group_goods)
        self.assertEqual(target.ddp_usd, 55)
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.scenario).count(), 1)

    def workbook(self, rows):
        wb = Workbook()
        wb.active.append(['Column'] * 14)
        for row in rows:
            wb.active.append(row)
        data = BytesIO()
        wb.save(data)
        return SimpleUploadedFile('goods.xlsx', data.getvalue())

    def excel_row(self, group, pk=None, price=8):
        return [pk, 'Ignored scenario name', group, 'Imported sales', 'Imported group', 'Brand', 'Supplier',
                self.kind.name, 1, 2, price, 4, 30, 20]

    def test_excel_without_ids_updates_group_instead_of_creating_case_duplicate(self):
        target = PGGoods.objects.create(**self.fields())
        response = self.client.post(f'{reverse("import_from_excel")}?scenario={self.scenario.pk}',
                                    {'excel_file': self.workbook([self.excel_row(' daikin Sensira ')])})
        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertEqual(target.ddp_usd, 8)
        self.assertEqual(target.planning_sales, 'Imported sales')
        self.assertEqual(PGGoods.objects.count(), 1)

    def test_conflicting_excel_rename_does_not_break_remaining_rows(self):
        target = PGGoods.objects.create(**self.fields())
        other = PGGoods.objects.create(**self.fields(planning_group='Another'))
        response = self.client.post(f'{reverse("import_from_excel")}?scenario={self.scenario.pk}', {'excel_file': self.workbook([
            self.excel_row('DAIKIN SENSIRA', pk=other.pk, price=99), self.excel_row('New group', price=88),
        ])})
        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(target.ddp_usd, 3)
        self.assertEqual(other.planning_group, 'Another')
        self.assertEqual(PGGoods.objects.get(planning_group='New group').ddp_usd, 88)

    def sql_source(self):
        cursor = Mock()
        columns = ['PlanningGroupSalesERP', 'PlanningKey', 'Group_1', 'BrandName', 'PlanningGroupOZP', 'Purch', 'FlagInPlan', 'Volume', 'PriceDDP']
        cursor.description = [(name,) for name in columns]
        cursor.fetchall.return_value = [('Changed sales', 'key', 'Changed group', 'Brand', 'DAIKIN SENSIRA', 'Supplier', 1, 1, 66)]
        connection = Mock()
        connection.cursor.return_value = cursor
        return connection, cursor

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views.pyodbc.connect')
    def test_sql_sync_matches_group_only_in_both_overwrite_modes(self, connect):
        target = PGGoods.objects.create(**self.fields())
        connection, _ = self.sql_source()
        connect.return_value = connection
        sync_pggoods_data_for_scenario(self.scenario)
        target.refresh_from_db()
        self.assertEqual(target.ddp_usd, 3)
        self.scenario.overwrite_existing = True
        sync_pggoods_data_for_scenario(self.scenario)
        target.refresh_from_db()
        self.assertEqual(target.ddp_usd, 66)
        self.assertEqual(target.planning_sales, 'Changed sales')
        self.assertEqual(PGGoods.objects.count(), 1)

    @patch('model_purch.management.commands.import_pggoods.connect_database')
    def test_import_command_only_clears_and_imports_selected_scenario(self, connect):
        PGGoods.objects.create(**self.fields())
        untouched = PGGoods.objects.create(**self.fields(scenario_plan=self.other))
        connection, cursor = self.sql_source()
        connect.return_value = connection, cursor
        call_command('import_pggoods', scenario=self.scenario.pk, clear=True, dry_run=False, stdout=StringIO())
        untouched.refresh_from_db()
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.scenario).count(), 1)
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.other).count(), 1)
