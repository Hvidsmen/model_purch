"""Execute the importer weight CTE on cancellation/return edge cases."""
import sqlite3
from decimal import Decimal as D
from django.test import SimpleTestCase
from django.core.exceptions import ValidationError
from .services.sales_plans import source_sql,validate_plan_segments


class PlanWeightTests(SimpleTestCase):
    def weights(self,rows):
        conn=sqlite3.connect(':memory:')
        try:
            conn.execute('CREATE TABLE fact (SubdivisionName TEXT,PlanningGroupSalesErp TEXT,GroupERP TEXT,Brand TEXT,Segment TEXT,USD REAL)')
            conn.executemany('INSERT INTO fact VALUES (?,?,?,?,?,?)',rows)
            sql=source_sql('planning')
            cte=sql.split('), positive_fact AS (',1)[1].split('), plan_ AS (',1)[0]
            return conn.execute('WITH positive_fact AS ('+cte+') SELECT * FROM fact_per').fetchall()
        finally:conn.close()

    def test_near_zero_net_does_not_explode_and_keys_share_business_name(self):
        rows=[('Branch','RAC','G','B','O0',100.),('Branch','RAC','G','B','O2',300.),
              ('Branch','RAC','G','B','O3',-399.999999999)]
        result=self.weights(rows)
        self.assertEqual(len(result),1)
        shares=result[0][4:]
        self.assertEqual(shares,(.25,0.,.75,0.,0.))
        self.assertEqual(sum(shares),1.)
        for amount in (D('1000'),D('-1000'),D(0)):
            validate_plan_segments(amount,[amount*D(str(v)) for v in shares])

    def test_all_returns_and_zero_history_fall_back_to_o0(self):
        for usd in (-100.,0.):
            self.assertEqual(self.weights([('B','RAC','G','B','O3',usd)])[0][4:],(1,0,0,0,0))

    def test_valid_positive_history_is_unchanged(self):
        rows=[('B','RAC','G','B',f'O{i}',float(i+1)) for i in range(5)]
        self.assertEqual(self.weights(rows)[0][4:],tuple((i+1)/15 for i in range(5)))

    def test_old_exploding_and_cancelling_segments_are_rejected(self):
        for segments in ([D('1e12'),0,0,D('-999999999900'),0],[110,-10,0,0,0],[1,-1,0,0,0]):
            with self.assertRaisesMessage(ValidationError,'Перезагрузите план'):
                validate_plan_segments(D(100) if sum(segments) else D(0),list(map(D,segments)))

    def test_both_sources_preserve_physical_receipts_and_use_name_weights(self):
        for source in ('planning','erp'):
            sql=source_sql(source)
            self.assertIn('GROUP BY SubdivisionName, PlanningGroupSalesErp, GroupERP, Brand',sql)
            self.assertIn('plan_.Subdivision=f.SubdivisionName',sql)
            self.assertIn('s.SubdivisionKey = ps.',sql)
            self.assertIn('sub.SubdivisionKey',sql)


from django.test import TestCase
from django.utils import timezone
from datetime import date
from unittest.mock import Mock
from .models import SalesPlanScenario,SalesPlanLine
from .services.sales_plans import load_plan
from .services.coefficient_iteration_calculation import plan_rows


class SavedPlanGuardTests(TestCase):
    def test_bad_import_preserves_previous_saved_plan(self):
        scenario=SalesPlanScenario.objects.create(title='Test',source_version='v',loaded_at=timezone.now())
        old=SalesPlanLine.objects.create(scenario=scenario,subdivision='Branch',plan_date=date(2027,1,1),amount_usd=100)
        conn,cursor=Mock(),Mock()
        cursor.description=[(v,) for v in ['Subdivision','SubdivisionKey','PlanningGroupSalesERP','GroupERP','Brand','Date_','AmountUSD',*[f'USD_O{i}' for i in range(5)]]]
        cursor.fetchmany.side_effect=[[('Branch','KEY','RAC','G','B',date(2027,1,1),100,1000000000000,0,0,-999999999900,0)],[]]
        with self.assertRaisesMessage(ValidationError,'Перезагрузите план'):
            load_plan(scenario.pk,lambda *args:(conn,cursor))
        self.assertEqual(list(scenario.lines.values_list('pk',flat=True)),[old.pk])
        conn.close.assert_called_once()

    def test_saved_exploding_parts_cannot_start_another_iteration(self):
        scenario=SalesPlanScenario.objects.create(title='Test',source_version='v',loaded_at=timezone.now())
        segments=dict(zip([f'O{i}' for i in range(5)],['1000000000000','0','0','-999999999900','0']))
        SalesPlanLine.objects.create(scenario=scenario,subdivision='Branch',plan_date=date(2027,1,1),amount_usd=100,
            segment_amounts=segments,source_subdivisions={'KEY':{'amount':'100','segments':segments}})
        with self.assertRaisesMessage(ValidationError,'Перезагрузите план'):
            plan_rows(scenario,2027)
