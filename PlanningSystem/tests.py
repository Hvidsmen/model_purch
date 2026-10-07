import shutil
import tempfile
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from . import views
from .models import OptionPlanRef, PeriodPlanRef
from .models_algorithm import PercentSubdivisionHeader, PercentSubdivisionSeason


class PercentageUploadTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.option = OptionPlanRef.objects.create(option_key='plan', option_name='Plan')
        self.period = PeriodPlanRef.objects.create(perion_plan_name='Month')

    def upload(self, name):
        return self.client.post(reverse(name), {
            'year': 2026, 'sheet_name': 'Загрузка', 'option': self.option.pk,
            'period': self.period.pk, 'file': SimpleUploadedFile('plan.xlsx', b'test'),
        })

    def test_routes_and_navigation_are_distinct(self):
        subdivision, season = reverse('load_percentage_subdivision'), reverse('load_percent_season')
        self.assertNotEqual(subdivision, season)
        self.assertIs(resolve(subdivision).func, views.load_percentage_subdivision)
        self.assertIs(resolve(season).func, views.load_percent_season)
        response = self.client.get(reverse('index'))
        self.assertContains(response, f'href="{subdivision}"')
        self.assertContains(response, f'href="{season}"')

    def test_pages_work_without_uploaded_templates(self):
        for name, template in [
            ('load_percentage_subdivision', 'PlanningSystem/load_percentage_subdivision.html'),
            ('load_percent_season', 'PlanningSystem/load_percent_season.html'),
        ]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, template)
            self.assertContains(response, f'action="{reverse(name)}"')

    @patch('PlanningSystem.views.PecentSubdivisionGoods.create_by_file')
    @patch('PlanningSystem.views.PercentSubdivisionSeason.create_by_file')
    def test_subdivision_upload_uses_correct_importer(self, season_import, subdivision_import):
        self.assertEqual(self.upload('load_percentage_subdivision').status_code, 200)
        self.assertIsInstance(subdivision_import.call_args.args[0], PercentSubdivisionHeader)
        season_import.assert_not_called()
        self.assertEqual(PercentSubdivisionHeader.objects.count(), 1)
        self.assertEqual(PercentSubdivisionSeason.objects.count(), 0)

    @patch('PlanningSystem.views.PecentSubdivisionGoods.create_by_file')
    @patch('PlanningSystem.views.PercentSubdivisionSeason.create_by_file')
    def test_season_upload_uses_correct_importer(self, season_import, subdivision_import):
        self.assertEqual(self.upload('load_percent_season').status_code, 200)
        self.assertIsInstance(season_import.call_args.args[0], PercentSubdivisionSeason)
        subdivision_import.assert_not_called()
        self.assertEqual(PercentSubdivisionSeason.objects.count(), 1)
        self.assertEqual(PercentSubdivisionHeader.objects.count(), 0)

    @patch('PlanningSystem.views.PecentSubdivisionGoods.create_by_file')
    def test_invalid_upload_keeps_existing_plan(self, importer):
        existing = PercentSubdivisionHeader.objects.create(opition_plan=self.option, year=2025)
        response = self.client.post(reverse('load_percentage_subdivision'), {'year': 'invalid'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertTrue(PercentSubdivisionHeader.objects.filter(pk=existing.pk).exists())
        importer.assert_not_called()


class SubdivisionHeaderTests(TestCase):
    def test_percentage_rows_belong_to_percentage_header(self):
        from .models import ChanelRef, GoodsRef, SubdivisionRef
        from .models_algorithm import PecentSubdivisionGoods
        option = OptionPlanRef.objects.create(option_key='plan', option_name='Plan')
        header = PercentSubdivisionHeader.objects.create(opition_plan=option)
        row = PecentSubdivisionGoods.objects.create(
            header=header, percent=20,
            chanel=ChanelRef.objects.create(chanel_key='channel', chanel_name='Channel'),
            goods=GoodsRef.objects.create(goods_key='goods'),
            subdivision=SubdivisionRef.objects.create(subdivision_key='branch', subdivision_name='Branch'),
        )
        row.refresh_from_db()
        self.assertEqual(row.header, header)
