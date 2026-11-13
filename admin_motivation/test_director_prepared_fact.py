from datetime import date
from decimal import Decimal as D
import json
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock,patch
from django.test import TestCase,SimpleTestCase
from django.core.exceptions import ValidationError
from .models import MotivationCalculationSchedule
from .services.director_prepared_fact import settings_for,read_fact,PROJECTION
from .services.director_scenario_inputs import BRANCHES


class SettingsTests(TestCase):
    def setUp(self):
        self.plan=SimpleNamespace(source_version='Selected plan')

    def job(self,**changes):
        return MotivationCalculationSchedule.objects.create(name='Main',variant='main',fact_year=2026,
            plan_version='Selected plan',quarter_share=changes.pop('quarter_share',65),
            year_share=changes.pop('year_share',35),payout_threshold='.9',**changes)

    def test_saved_unexecuted_disabled_job_supplies_parameters(self):
        job=self.job(enabled=False)
        result=settings_for(self.plan)
        self.assertEqual(D(result['quarter_share']),65)
        self.assertEqual(D(result['year_share']),35)
        self.assertEqual(D(result['payout_threshold']),D('.9'))
        self.assertEqual(result['settings_job_key'],str(job.key))

    def test_missing_matching_main_job_is_explicit(self):
        self.job();self.plan.source_version='Other'
        with self.assertRaisesMessage(ValidationError,'Запускать расчёт премий не нужно'):settings_for(self.plan)

    def test_multiple_main_jobs_rejected_instead_of_arbitrary_settings(self):
        self.job();self.job()
        with self.assertRaisesMessage(ValidationError,'несколько'):settings_for(self.plan)

    def test_invalid_shares_rejected(self):
        self.job(quarter_share=90,year_share=20)
        with self.assertRaisesMessage(ValidationError,'неверные'):settings_for(self.plan)


class PreparedReadTests(SimpleTestCase):
    def setUp(self):
        check=patch('admin_motivation.services.director_prepared_fact.require_columns')
        self.schema_check=check.start();self.addCleanup(check.stop)
        self.cursor=Mock()
        self.cursor.fetchone.side_effect=[(1,1,1),('prepared-run','completed-at')]
        self.cursor.fetchall.return_value=[(BRANCHES[0],1,'1. RAC','AC','B','O2',100,-20,1)]
        self.version=Mock();self.version.discount_segments.order_by.return_value.values.return_value=[
            {'type_discount':'MASS','o0':0,'o1':10,'o2':20,'o3':30,'o4':40}]

    def test_ready_snapshot_and_selected_segments_with_no_premium_run(self):
        rows,source=read_fact(self.cursor,self.version,BRANCHES,{'quarter_share':'65'})
        self.assertEqual(rows[0]['negative'],'-20')
        self.assertEqual(source['run_id'],'prepared-run')
        self.assertEqual(source['parameters']['quarter_share'],'65')
        sql='\n'.join(c.args[0] for c in self.cursor.execute.call_args_list)
        for forbidden in ('FactAll2026','PortalMotivationRun','dbo.Sales ','sp_CalcBySub','vSubdivisionMotiveCoeffTo'):
            self.assertNotIn(forbidden,sql)
        self.assertIn('r.SourceSchema=',sql)
        call=self.cursor.execute.call_args
        self.assertEqual(call.args[1:5],('20260101','20261001',*BRANCHES))
        self.assertEqual(self.cursor.executemany.call_args.args[1][0],('MASS',10,20,30,40))
        self.assertNotIn('OPENJSON',sql)

    def test_not_ready_or_old_schema_no_raw_sales_fallback(self):
        self.cursor.fetchone.side_effect=[(1,1,1),None]
        with self.assertRaisesMessage(ValidationError,'совместимого'):read_fact(self.cursor,self.version,BRANCHES,{})
        self.assertEqual(self.cursor.execute.call_count,2)

    def test_no_tables_or_segments_explicit(self):
        self.cursor.fetchone.side_effect=[(None,None,None)]
        with self.assertRaisesMessage(ValidationError,'подготовьте'):read_fact(self.cursor,self.version,BRANCHES,{})
        self.version.discount_segments.order_by.return_value.values.return_value=[]
        with self.assertRaisesMessage(ValidationError,'сегменты'):read_fact(self.cursor,self.version,BRANCHES,{})

    def test_ambiguous_discount_patterns_never_multiply_fact(self):
        self.cursor.fetchall.return_value=[(BRANCHES[0],1,'PG','G','B','O1',100,0,2)]
        with self.assertRaisesMessage(ValidationError,'несколько типов'):read_fact(self.cursor,self.version,BRANCHES,{})

    def test_empty_fact_not_reported_as_zero(self):
        self.cursor.fetchall.return_value=[]
        with self.assertRaisesMessage(ValidationError,'нет продаж'):read_fact(self.cursor,self.version,BRANCHES,{})


class ProjectionTests(SimpleTestCase):
    """Execute the actual physical aggregation and segment CASE in SQLite.

    Only the dialect-specific OUTER APPLY configuration reader and
    DATEPART syntax are adapted; fact arithmetic and grouping stay unchanged.
    """
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.create_function('DATEPART',2,lambda kind,day:(int(day[4:6])-1)//3+1)
        self.db.executescript('''CREATE TABLE sales(Subdivision TEXT,ProductCode TEXT,CustomerKey TEXT,
 ClosedDealDate TEXT,DocumentNumber TEXT,DateInvoice TEXT,DiscountMotivation NUMERIC,
 ДаичиТипСкидкиНаДату TEXT,ВидЦены TEXT,даичи_ВидЦеныВходнаяСтоимостьНаименование TEXT,
 Price NUMERIC,AmountUSDIt_ITW NUMERIC,BallsOuterUSD NUMERIC);
 CREATE TABLE subs(SubdivisionKey TEXT,SubdivisionName TEXT,Chanel TEXT);
 CREATE TABLE products(ProductCode TEXT,PlanningGroupSalesErp TEXT,GroupERP TEXT,BrandName TEXT,
 ModelCode TEXT,[Тип номенклатуры] TEXT);
 CREATE TABLE customers(CustomerKey TEXT,CustomerKeyName TEXT);
 CREATE TABLE thresholds(type_discount TEXT,o1 NUMERIC,o2 NUMERIC,o3 NUMERIC,o4 NUMERIC,matches INT);
 INSERT INTO subs VALUES('M','Д-Москва','ДПП'),('T','ТЕРМОТРЕЙД','Dealer'),('X','Other','Dealer');
 INSERT INTO products VALUES('P','1. RAC','Бытовые кондиционеры','Brand','Model','Товар'),
 ('H','4. HEAT','Other','Brand','Heater','Товар'),('S','1. RAC','Other','Brand','Service','Услуга');
 INSERT INTO customers VALUES('C','Customer');
 INSERT INTO thresholds VALUES('MASS',10,20,30,40,1);''')
        query=PROJECTION.replace('__DIRECTOR_PARTICIPATION__','1=1').replace('DataWH.motivation.PortalPreparedSales','sales').replace('DataWH.dbo.Subdivisions','subs').replace('DataWH.dbo.Product','products').replace('DataWH.dbo.Customer','customers').replace('DATEPART(QUARTER,',"DATEPART('QUARTER',")
        self.query=re.sub(r" OUTER APPLY \(SELECT COUNT\(\*\).*?\) m", " LEFT JOIN thresholds m ON f.discount_type LIKE '%' || m.type_discount || '%'",query,flags=re.S)

    def sale(self,price,*,doc='Doc',product='P',branch='M',day='20260110',discount=20,itw=0,balls=0,kind='MASS'):
        self.db.execute('INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (branch,product,'C',day,doc,day,discount,kind,'Retail','Retail',price,itw,balls))

    def rows(self):return self.db.execute(self.query,('20260101','20261001',*BRANCHES)).fetchall()

    def test_usd_base_keeps_balls_and_nets_invoice_before_sign(self):
        self.sale(120,itw=30,balls=10);self.sale(-30)
        self.sale(-20,doc='Return')
        row=self.rows()[0]
        self.assertEqual(row[:6],(BRANCHES[0],1,'1. RAC','Бытовые кондиционеры','Brand','O2'))
        self.assertEqual(row[6:8],(70,-20))

    def test_date_original_branch_product_filter_and_heat_dpp_included(self):
        self.sale(100,product='H');self.sale(200,branch='T',day='20260930')
        self.sale(900,day='20261001');self.sale(900,day='20251231')
        self.sale(900,branch='X');self.sale(900,product='S')
        rows=self.rows()
        self.assertEqual(len(rows),2);self.assertEqual(sum(r[6] for r in rows),300)
        self.assertEqual({r[0] for r in rows},set(BRANCHES))

    def test_all_brand_bearing_groups_and_brandless_groups_follow_the_same_rule(self):
        groups=('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит',
                'Системы VRF','Мобильные кондиционеры','Оконные кондиционеры')
        for i,group in enumerate(groups):
            self.db.execute('INSERT INTO products VALUES (?,?,?,?,?,?)',(f'C{i}','1. RAC',group,'Brand',f'Model{i}','Товар'))
            self.sale(100,product=f'C{i}',doc=f'Class{i}')
        self.assertEqual({r[3]:r[4] for r in self.rows()},
            {group:'Brand' if i<4 else '' for i,group in enumerate(groups)})

    def test_threshold_boundaries_and_unknown_type_use_original_o0_rule(self):
        for i,value in enumerate((9,10,20,30,40)):
            self.sale(10,doc=str(i),discount=value)
        self.sale(15,doc='Unknown',kind='Other',discount=99)
        self.assertEqual({r[5]:r[6] for r in self.rows()}, {'O0':25,'O1':10,'O2':10,'O3':10,'O4':10})


class SourceSchemaTests(SimpleTestCase):
    def test_missing_columns_are_reported_together_before_projection(self):
        from .services.director_input_schema import require_columns
        cursor=Mock();cursor.fetchall.return_value=[('dbo','Product','ProductCode')]
        with self.assertRaisesMessage(ValidationError,'dbo.Product.BrandName') as error:
            require_columns(cursor,('dbo.Product',))
        self.assertIn('dbo.Product.ModelCode',str(error.exception))
        self.assertEqual(cursor.execute.call_args.args[1:],('dbo.Product',))

    def test_catalog_identifier_case_does_not_change_column_readiness(self):
        from .services.director_input_schema import require_columns,COLUMNS
        cursor=Mock();cursor.fetchall.return_value=[('DBO','PRODUCT',name.upper()) for name in COLUMNS['dbo.Product']]
        require_columns(cursor,('dbo.Product',))


class PlanClassificationTests(TestCase):
    def test_plan_uses_identical_brand_whitelist_and_group_collapse(self):
        from django.utils import timezone
        from .models import SalesPlanScenario,SalesPlanLine
        from .services.director_scenario_inputs import plan_inputs
        plan=SalesPlanScenario.objects.create(title='Classification',source_version='Class',loaded_at=timezone.now())
        groups=('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит',
                'Системы VRF','Мобильные кондиционеры','Оконные кондиционеры')
        for i,group in enumerate(groups):
            SalesPlanLine.objects.create(scenario=plan,plan_date=date(2026,1,1),subdivision=BRANCHES[0],
                planning_group_sales='1. RAC',group=group,brand='Brand',amount_usd=100,
                segment_amounts={'O0':'100',**{f'O{k}':'0' for k in range(1,5)}})
        SalesPlanLine.objects.create(scenario=plan,plan_date=date(2026,1,1),subdivision=BRANCHES[1],
            planning_group_sales='Other',group='Accessories',brand='Manufacturer',amount_usd=100,
            segment_amounts={'O0':'100',**{f'O{k}':'0' for k in range(1,5)}})
        rows=plan_inputs(plan,2026)
        self.assertEqual({r['goods']:r['brand'] for r in rows if r['branch']==BRANCHES[0]},
            {group:'Brand' if i<4 else '*' for i,group in enumerate(groups)})
        self.assertTrue(all(r['goods']=='*' and r['brand']=='*' for r in rows if r['branch']==BRANCHES[1]))
