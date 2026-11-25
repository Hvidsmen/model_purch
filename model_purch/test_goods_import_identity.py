from django.test import TestCase
from django.urls import reverse
from . import test_goods_uniqueness as fixtures
from .models import PGGoods

class GoodsImportIdentityTests(TestCase):
    setUp=fixtures.GoodsUniquenessTests.setUp
    fields=fixtures.GoodsUniquenessTests.fields
    workbook=fixtures.GoodsUniquenessTests.workbook
    excel_row=fixtures.GoodsUniquenessTests.excel_row

    def test_name_match_preserves_existing_parameters_without_id(self):
        target=PGGoods.objects.create(**self.fields(percent_stock_end=.25),container_volume=75,weight_kg=2.5,duty_rate=7)
        row=self.excel_row(' daikin Sensira ')
        response=self.client.post(f'{reverse("import_from_excel")}?scenario={self.scenario.pk}',{'excel_file':self.workbook([row])})
        self.assertEqual(response.status_code,302)
        target.refresh_from_db()
        self.assertEqual(target.percent_stock_end,.25)
        self.assertEqual(target.container_volume,75)
        self.assertEqual(target.weight_kg,2.5)
        self.assertEqual(target.duty_rate,7)
        self.assertEqual(target.planning_sales,'Imported sales')
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.scenario).count(),1)

    def test_foreign_scenario_id_matches_only_name_in_selected_scenario(self):
        target=PGGoods.objects.create(**self.fields(percent_stock_end=.3))
        foreign=PGGoods.objects.create(**self.fields(scenario_plan=self.other,percent_stock_end=.7))
        self.client.post(f'{reverse("import_from_excel")}?scenario={self.scenario.pk}',{'excel_file':self.workbook([self.excel_row('Daikin SENSIRA',pk=foreign.pk)])})
        target.refresh_from_db();foreign.refresh_from_db()
        self.assertEqual(target.percent_stock_end,.3)
        self.assertEqual(foreign.percent_stock_end,.7)
        self.assertEqual(foreign.planning_sales,'Sales')

    def test_conflicting_id_is_ignored_and_name_match_updated(self):
        target=PGGoods.objects.create(**self.fields())
        other=PGGoods.objects.create(**self.fields(planning_group='Other group'))
        self.client.post(f'{reverse("import_from_excel")}?scenario={self.scenario.pk}',{'excel_file':self.workbook([self.excel_row('Daikin SENSIRA',pk=other.pk)])})
        target.refresh_from_db();other.refresh_from_db()
        self.assertEqual(target.planning_sales,'Imported sales');self.assertEqual(other.planning_group,'Other group')
        self.assertEqual(other.planning_sales,'Sales')

    def test_new_name_with_existing_id_creates_new_group(self):
        original=PGGoods.objects.create(**self.fields())
        self.client.post(f'{reverse("import_from_excel")}?scenario={self.scenario.pk}',{'excel_file':self.workbook([self.excel_row('Brand new',pk=original.pk)])})
        original.refresh_from_db()
        self.assertEqual(original.planning_group,'Daikin SENSIRA')
        self.assertEqual(original.planning_sales,'Sales')
        self.assertTrue(PGGoods.objects.filter(scenario_plan=self.scenario,planning_group='Brand new').exists())

    def test_nonnumeric_id_is_ignored(self):
        target=PGGoods.objects.create(**self.fields(percent_stock_end=.4))
        self.client.post(f'{reverse("import_from_excel")}?scenario={self.scenario.pk}',{'excel_file':self.workbook([self.excel_row(' DAIKIN SENSIRA ',pk='old-id')])})
        target.refresh_from_db()
        self.assertEqual(target.planning_sales,'Imported sales')
        self.assertEqual(target.percent_stock_end,.4)
