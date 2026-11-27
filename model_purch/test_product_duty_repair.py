import csv
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from .models import PGGoods, GoodsGroup, ScenarioModel, KindPurch, Freight
from .services.product_duty_repair import read_targets, repair_targets, COLUMNS


class ProductDutyRepairTests(TestCase):
    def setUp(self):
        self.scenario=ScenarioModel.objects.create(name='Addressed repair',date_start_plan='2026-01-01',date_end_plan='2026-12-31')
        self.kind=KindPurch.objects.create(name='Repair import')
        self.group=GoodsGroup.objects.create(name='Group',duty_rate=0,container_volume=54)
        self.freight=Freight.objects.create(scenario=self.scenario,price_per_container=10500,customs_rate=22,warehouse_delivery_cost=700)
        self.goods=[PGGoods.objects.create(scenario_plan=self.scenario,planning_group='Item '+str(i),planning_sales='Plan',group_goods='Group',
                     kind_purch=self.kind,volume=36,container_volume=54,duty_rate=rate,exw_usd=17589.36849,stock_cnt_day=0,percent_stock_end=0)
                    for i,rate in enumerate((5,6.5,9,10))]
        self.targets=[dict(id=g.pk,scenario_id=self.scenario.pk,group=g.group_goods,product=g.planning_group,old_rate=str(g.duty_rate),new_rate=str(g.duty_rate/100)) for g in self.goods]

    def test_preview_then_apply_only_exact_ids_no_groups_or_backups(self):
        extra=PGGoods.objects.get(pk=self.goods[0].pk);extra.pk=None;extra.planning_group='Other';extra.save()
        preview=repair_targets(self.targets)
        self.assertEqual(preview['changed'],4)
        self.goods[0].refresh_from_db();self.assertEqual(self.goods[0].duty_rate,5)
        result=repair_targets(self.targets,apply=True,expected_preview=preview['preview_hash'])
        self.assertTrue(result['applied']);self.assertFalse(result['backups_created'])
        for good,rate in zip(self.goods,(.05,.065,.09,.1)):
            good.refresh_from_db();self.assertEqual(good.duty_rate,rate)
        self.assertAlmostEqual(self.goods[-1].ddp_usd,31006.662331246666)
        self.group.refresh_from_db();self.assertEqual(self.group.duty_rate,0)
        extra.refresh_from_db();self.assertEqual(extra.duty_rate,5)

    def test_apply_requires_preview_and_stale_price_dependencies_are_rejected(self):
        with self.assertRaises(ValidationError):repair_targets(self.targets,apply=True)
        preview=repair_targets(self.targets)
        self.freight.price_per_container=9999;self.freight.save()
        with self.assertRaises(ValidationError):repair_targets(self.targets,apply=True,expected_preview=preview['preview_hash'])
        self.assertEqual(list(PGGoods.objects.order_by('pk').values_list('duty_rate',flat=True)),[5,6.5,9,10])

    def test_identity_archive_and_unexpected_manual_rate_changes_fail_whole_batch(self):
        for field,value in [('planning_group','Renamed'),('group_goods','Other'),('duty_rate',.07)]:
            good=self.goods[-1];old=getattr(good,field)
            PGGoods.objects.filter(pk=good.pk).update(**{field:value})
            with self.assertRaises(ValidationError):repair_targets(self.targets)
            PGGoods.objects.filter(pk=good.pk).update(**{field:old})
        self.scenario.archived_at=timezone.now();self.scenario.save()
        with self.assertRaises(ValidationError):repair_targets(self.targets)
        self.goods[0].refresh_from_db();self.assertEqual(self.goods[0].duty_rate,5)

    def test_repeat_does_not_divide_again_and_stale_costs_are_recomputed(self):
        preview=repair_targets(self.targets)
        repair_targets(self.targets,apply=True,expected_preview=preview['preview_hash'])
        repeated=repair_targets(self.targets)
        self.assertEqual(repeated['changed'],0);self.assertEqual(len(repeated['already_correct']),4)
        PGGoods.objects.filter(pk=self.goods[-1].pk).update(ddp_usd=99999)
        preview=repair_targets(self.targets)
        self.assertEqual(preview['changed'],1)
        repair_targets(self.targets,apply=True,expected_preview=preview['preview_hash'])
        self.goods[-1].refresh_from_db();self.assertEqual(self.goods[-1].duty_rate,.1)
        self.assertAlmostEqual(self.goods[-1].ddp_usd,31006.662331246666)

    def test_write_failure_rolls_back_every_item(self):
        preview=repair_targets(self.targets)
        def fail(*args,**kwargs):
            PGGoods.objects.filter(pk=self.goods[0].pk).update(duty_rate=.05)
            raise RuntimeError('late failure')
        with patch.object(PGGoods.objects,'bulk_update',side_effect=fail):
            with self.assertRaises(RuntimeError):repair_targets(self.targets,apply=True,expected_preview=preview['preview_hash'])
        self.goods[0].refresh_from_db();self.assertEqual(self.goods[0].duty_rate,5)

    def write_csv(self,path,rows):
        with path.open('w',encoding='utf-8-sig',newline='') as output:
            writer=csv.DictWriter(output,fieldnames=COLUMNS,delimiter=';');writer.writeheader();writer.writerows(rows)

    def audit_rows(self):
        return [dict(zip(COLUMNS,('PRODUCT',g.pk,self.scenario.pk,'Group',g.planning_group,g.duty_rate,'Пошлина выше 100%; отличается от группы'))) for g in self.goods]

    def test_command_filters_archives_and_normal_rates_and_creates_no_files(self):
        rows=self.audit_rows()
        rows.extend([dict(rows[0],**{'ID товара':'99999','Проверка':'АРХИВ; Пошлина выше 100%'}),dict(rows[0],**{'ID товара':'99998','Сохранённый коэффициент':'.05'})])
        with TemporaryDirectory() as directory:
            path=Path(directory)/'audit.csv';self.write_csv(path,rows)
            targets,archived,other=read_targets(path)
            self.assertEqual((len(targets),archived,other),(4,1,1))
            output=StringIO();call_command('repair_product_duties',audit=str(path),json_output=True,stdout=output)
            preview=json.loads(output.getvalue());self.assertEqual(preview['selected'],4)
            output=StringIO();call_command('repair_product_duties',audit=str(path),apply=True,expected_preview=preview['preview_hash'],stdout=output)
            self.assertIn('Резервные копии не создавались',output.getvalue())
            self.assertEqual(list(Path(directory).iterdir()),[path])

    def test_unsupported_and_duplicate_rows_rejected(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'audit.csv';rows=self.audit_rows()
            self.write_csv(path,[rows[0],rows[0]])
            with self.assertRaises(ValidationError):read_targets(path)
            self.write_csv(path,[dict(rows[0],**{'Сохранённый коэффициент':'1.5'})])
            with self.assertRaises(ValidationError):read_targets(path)
