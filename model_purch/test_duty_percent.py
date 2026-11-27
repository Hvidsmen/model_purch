from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone
from openpyxl import load_workbook
from base.testing import authorize_admin
from .forms import GoodsGroupForm
from .models import PGGoods, GoodsGroup, ScenarioModel, KindPurch, Freight
from .services.pricing import apply_group_duty
from .services.duty_repair import repair


class DutyPercentTests(TestCase):
    def setUp(self):
        authorize_admin(self.client)
        self.scenario=ScenarioModel.objects.create(name='Duty test',date_start_plan='2026-01-01',date_end_plan='2026-12-31')
        self.kind=KindPurch.objects.create(name='Duty imported')
        self.group=GoodsGroup.objects.create(name='Chillers',duty_rate=.1,container_volume=54)
        Freight.objects.create(scenario=self.scenario,price_per_container=10500,customs_rate=22,warehouse_delivery_cost=700)
        self.good=PGGoods.objects.create(scenario_plan=self.scenario,planning_group='Чиллеры_MIDEA',planning_sales='3. PROF',
            group_goods='Chillers',kind_purch=self.kind,volume=36,container_volume=54,duty_rate=.1,exw_usd=17589.36849,stock_cnt_day=0,percent_stock_end=0)

    def test_group_form_percent_input_display_and_repeated_save(self):
        form=GoodsGroupForm(instance=self.group)
        self.assertEqual(form.initial['duty_rate'],10)
        for value,expected in [('10',.1),('0.1',.001),('0',0),('7.25',.0725)]:
            form=GoodsGroupForm({'name':'Chillers','container_volume':54,'duty_rate':value},instance=self.group)
            self.assertTrue(form.is_valid(),form.errors)
            self.group=form.save();self.group.refresh_from_db()
            self.assertAlmostEqual(self.group.duty_rate,expected)
        self.assertFalse(GoodsGroupForm({'name':'Chillers','container_volume':54,'duty_rate':'-1'},instance=self.group).is_valid())

    def test_directory_ten_percent_transfers_to_goods_without_double_division(self):
        response=self.client.post(reverse('goods_groups'),{'id':self.group.pk,'name':'Chillers','container_volume':54,'duty_rate':'10'})
        self.assertEqual(response.status_code,302)
        self.group.refresh_from_db();self.assertEqual(self.group.duty_rate,.1)
        PGGoods.objects.filter(pk=self.good.pk).update(duty_rate=0)
        apply_group_duty(self.group.pk)
        self.good.refresh_from_db()
        self.assertEqual(self.good.duty_rate,.1)
        self.assertAlmostEqual(self.good.ddp_usd,31006.662331246666)
        page=self.client.get(reverse('goods_groups'))
        self.assertContains(page,'10%')
        self.assertNotContains(page,'Пошлина, коэффициент')

    def test_excel_export_import_preserves_percentage_fraction(self):
        response=self.client.get(reverse('export_pggoods'),{'scenario':self.scenario.pk})
        content=b''.join(response.streaming_content) if response.streaming else response.content
        book=load_workbook(BytesIO(content));sheet=book.active
        self.assertEqual(sheet['P2'].value,.1)
        self.assertEqual(sheet['P2'].number_format,'0.00%')
        book.close()
        upload=SimpleUploadedFile('duty.xlsx',content,content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response=self.client.post(reverse('import_from_excel')+f'?scenario={self.scenario.pk}',{'excel_file':upload})
        self.assertEqual(response.status_code,302)
        self.good.refresh_from_db();self.assertEqual(self.good.duty_rate,.1)
        self.assertAlmostEqual(self.good.ddp_usd,31006.662331246666)

    def corrupt(self):
        self.group.duty_rate=10;self.group.save()
        self.good.duty_rate=10;self.good.save()

    def test_repair_preview_no_writes_then_explicit_backup_and_apply(self):
        self.corrupt()
        self.good.refresh_from_db();self.assertAlmostEqual(self.good.ddp_usd,84562.30690246666)
        preview=repair('Chillers',10,10)
        self.group.refresh_from_db();self.good.refresh_from_db()
        self.assertEqual(self.group.duty_rate,10);self.assertEqual(self.good.duty_rate,10)
        self.assertAlmostEqual(preview['goods'][0]['after']['ddp_usd'],31006.662331246666)
        with TemporaryDirectory() as directory:
            backup=Path(directory)/'repair.json'
            result=repair('Chillers',10,10,apply=True,backup_file=backup)
            self.assertTrue(result['applied']);self.assertTrue(backup.exists())
            import json
            self.assertEqual(json.loads(backup.read_text())['goods'][0]['before']['duty_rate'],10)
        self.group.refresh_from_db();self.good.refresh_from_db()
        self.assertEqual(self.group.duty_rate,.1);self.assertEqual(self.good.duty_rate,.1)
        with self.assertRaises(ValidationError):repair('Chillers',10,10)

    def test_repair_preserves_individual_rates_and_archives(self):
        self.corrupt()
        individual=PGGoods.objects.get(pk=self.good.pk);individual.pk=None;individual.planning_group='Individual';individual.duty_rate=.05;individual.save()
        archived=ScenarioModel.objects.create(name='Archived duty',date_start_plan='2025-01-01',date_end_plan='2025-12-31',archived_at=timezone.now())
        old=PGGoods.objects.get(pk=self.good.pk);old.pk=None;old.scenario_plan=archived;old.save()
        with TemporaryDirectory() as directory:repair('Chillers',10,10,apply=True,backup_file=Path(directory)/'backup.json')
        individual.refresh_from_db();old.refresh_from_db()
        self.assertEqual(individual.duty_rate,.05);self.assertEqual(old.duty_rate,10)

    def test_repair_refuses_to_write_without_backup_and_lists_suspects(self):
        self.corrupt()
        with self.assertRaises(ValidationError):repair('Chillers',10,10,apply=True)
        self.group.refresh_from_db();self.assertEqual(self.group.duty_rate,10)
        output=StringIO();call_command('repair_duty_percent',stdout=output)
        self.assertIn('Chillers',output.getvalue())
        output=StringIO();call_command('repair_duty_percent',group='Chillers',from_rate='10',to_percent='10',stdout=output)
        self.assertIn('Данные не изменены',output.getvalue())
