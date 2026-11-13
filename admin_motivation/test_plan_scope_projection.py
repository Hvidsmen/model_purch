from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from .models import SalesPlanScenario,SalesPlanLine
from .services.plan_scope import compact_originals


class CompactRateProjectionTests(TestCase):
    def test_projection_reads_only_saved_rates_and_recipient_scopes(self):
        scenario=SalesPlanScenario.objects.create(title='Projection',source_version='saved')
        records={kind:{f'O{i}':{'coefficient':str(Decimal(i)/100),'provenance':'audit '*1000} for i in range(5)} for kind in ('Политики','Продажи')}
        employee={'employee_id':1,'subdivisions':['A'],'coefficients':['.01']*5}
        records['Директора']={'employees':[employee]}
        records['unused']=[{'details':'large audit'}]*1000
        SalesPlanLine.objects.create(scenario=scenario,subdivision='',plan_date=date(2027,1,1),amount_usd=100,total_usd=5,calculation=records)
        with CaptureQueriesContext(connection) as captured:
            originals=list(compact_originals(scenario.lines.filter(subdivision='')))
        self.assertEqual(len(captured),1)
        item=originals[0]
        self.assertEqual(item.total_usd,5)
        self.assertEqual(item.calculation['Директора']['employees'],[employee])
        self.assertEqual(item.calculation['Политики']['O4'],{'coefficient':'0.04'})
        self.assertNotIn('unused',item.calculation)
        self.assertNotIn('provenance',item.calculation['Продажи']['O0'])
        self.assertIn('json_extract',captured[0]['sql'].lower())

    def test_missing_saved_coefficients_remain_missing(self):
        scenario=SalesPlanScenario.objects.create(title='Old',source_version='saved')
        SalesPlanLine.objects.create(scenario=scenario,subdivision='',plan_date=date(2027,1,1),amount_usd=100,calculation={})
        item=next(compact_originals(scenario.lines.all()))
        self.assertEqual(item.calculation['Политики'],{})
        self.assertNotIn('Директора',item.calculation)

    def test_director_base_is_computed_once_per_source_row(self):
        from unittest.mock import patch
        from .services.motivation_exclusions import amounts_for
        from .services.plan_scope import global_lines_without_excluded
        scenario=SalesPlanScenario.objects.create(title='Bases',source_version='saved')
        SalesPlanLine.objects.create(scenario=scenario,subdivision='A',plan_date=date(2027,1,1),amount_usd=100,
            segment_amounts={f'O{i}':'100' if i==0 else '0' for i in range(5)})
        with patch('admin_motivation.services.motivation_exclusions.amounts_for',wraps=amounts_for) as bases:
            rows=global_lines_without_excluded(scenario,excluded={'Вентиляция'},summary_only=True)
        self.assertEqual(len(rows),1)
        self.assertEqual(sum(call.args[0]=='Директора' for call in bases.call_args_list),1)
