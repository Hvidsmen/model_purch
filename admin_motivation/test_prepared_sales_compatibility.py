import random
import sqlite3
import struct
from unittest.mock import Mock,patch
from django.test import SimpleTestCase,TestCase
from django.core.exceptions import ValidationError
from .services import prepared_sales as sales
from .services.prepared_sales_compatibility import DOCUMENT_GUARD,LEGACY_CASE,discount_case


def legacy_definition():
    source=sales.canonical_preparation()
    case=discount_case(source)
    return (source[:case.start('case')]+LEGACY_CASE+source[case.end('case'):]).replace(DOCUMENT_GUARD,'')+';WITH fact AS (SELECT * FROM #portal_motivation_sales) SELECT * FROM fact;'


class LegacyPreparationTests(SimpleTestCase):
    def test_reviewed_legacy_uses_snapshot_after_float_type_check(self):
        for type_id in (59,62):
            cursor=Mock();cursor.fetchone.return_value=(type_id,)
            sql=sales.patch(legacy_definition(),cursor=cursor)
            self.assertIn(sales.MARKER,sql);self.assertIn(sales.TABLE,sql)
            self.assertNotIn('SET s.DiscountMotivation',sql)
            self.assertNotIn('FROM DataWH.dbo.Sales',sql)
            cursor.execute.assert_called_once()
            self.assertIn("name=N'Discount'",cursor.execute.call_args.args[0])

    def test_canonical_math_without_old_duplicate_guard_needs_no_type_assumption(self):
        source=sales.canonical_preparation().replace(DOCUMENT_GUARD,'')+';WITH fact AS (SELECT 1)'
        cursor=Mock();sql=sales.patch(source,cursor=cursor)
        self.assertIn(sales.MARKER,sql)
        self.assertFalse(any('SELECT system_type_id' in call.args[0] for call in cursor.execute.call_args_list))
        # The producer's independent conflict validation is still present.
        producer=sales.procedure_sql()
        self.assertIn('COUNT_BIG(*) > 1',producer)
        self.assertIn('PortalPreparedSalesConflict',producer)

    def test_integer_decimal_money_or_missing_type_is_rejected(self):
        for row in (None,(56,),(106,),(108,),(60,),(122,)):
            cursor=Mock();cursor.fetchone.return_value=row
            with self.subTest(row=row),self.assertRaisesMessage(ValidationError,'real/float'):
                sales.patch(legacy_definition(),cursor=cursor)
        with self.assertRaisesMessage(ValidationError,'проверить тип'):
            sales.patch(legacy_definition())

    def test_changed_financial_math_or_guards_or_join_still_rejected(self):
        original=legacy_definition()
        for source in (original.replace('s.BallsOuterUSD','0'),
                       original.replace('s.Discount/100','s.Discount/99'),
                       original.replace("WHEN s.ВидЦены != '' and (1-s.Discount/100) =0 THEN s.Discount",''),
                       original.replace('phe.Price !=0','phe.Price >=0'),
                       original.replace('ds.СпособДоставки','N\'Other\'')):
            cursor=Mock();cursor.fetchone.return_value=(59,)
            with self.assertRaisesMessage(ValidationError,'отличается'):sales.patch(source,cursor=cursor)
            cursor.execute.assert_not_called()


class DiscountArithmeticTests(SimpleTestCase):
    def test_actual_cases_agree_for_real_inputs_nulls_zeroes_returns_and_random_values(self):
        canonical=sales.canonical_preparation();match=discount_case(canonical)
        safe=canonical[match.start('case'):match.end('case')]
        def adapt(case):
            return case.replace('DataWH.dbo.getCurrancyRateUSD','rateUSD').replace("N'","'")
        with sqlite3.connect(':memory:') as db:
            db.create_function('rateUSD',2,lambda day,currency:1.2)
            db.executescript('''CREATE TABLE s(ВидЦены TEXT,Discount REAL,Price REAL,AmountUSDIt_ITW REAL,
 BallsOuterUSD REAL,DateInvoice TEXT,Quantity REAL);
 CREATE TABLE phe(Price REAL,CurrencyCode TEXT);''')
            fixed=[('Retail',20,100,0,5,1,100),('Retail',100,100,0,0,1,100),
                ('Retail',0,0,0,8,1,100),('Retail',None,100,0,5,1,100),
                ('Retail',20,-100,0,5,1,100),('',20,100,0,5,0,100),
                ('',20,100,0,5,1,0),('',20,100,0,5,1,None),('',20,100,0,5,2,100),
                ('Retail',20,None,0,5,1,100),(None,20,100,0,5,1,100)]
            rng=random.Random(2025)
            samples=fixed+[(rng.choice(['Retail','']),rng.uniform(-10,110),rng.uniform(-1000,1000),
                rng.uniform(0,200),rng.uniform(-50,50),rng.choice([0,1,2]),rng.uniform(0,2000)) for _ in range(300)]
            def real(value):return struct.unpack('f',struct.pack('f',value))[0] if value is not None else None
            for price_type,discount,price,itw,balls,quantity,reference in samples:
                db.execute('DELETE FROM s');db.execute('DELETE FROM phe')
                db.execute('INSERT INTO s VALUES (?,?,?,?,?,?,?)',(price_type,real(discount),real(price),itw,balls,'20260101',quantity))
                db.execute('INSERT INTO phe VALUES (?,?)',(reference,'USD'))
                before,after=db.execute('SELECT '+adapt(LEGACY_CASE)+','+adapt(safe)+' FROM s CROSS JOIN phe').fetchone()
                self.assertEqual(before,after)


class LegacyHelperIntegrationTests(TestCase):
    def test_main_all_and_separate_private_preparation_accept_reviewed_helper(self):
        from .services.calculation_jobs import prepare
        from .services.recalculation_2025_sql import template_source
        source=template_source()
        start=source.index("IF OBJECT_ID('tempdb..#document_sales')")
        end=source.index("IF OBJECT_ID('tempdb..#fact')")
        helper_body=source[start:end]
        case=discount_case(helper_body)
        helper_body=(helper_body[:case.start('case')]+LEGACY_CASE+helper_body[case.end('case'):]).replace(DOCUMENT_GUARD,'')
        helper='CREATE PROCEDURE motivation.sp_PrepareSales AS BEGIN\n'+helper_body+'\nEND;'
        definition=source[:start]+'EXEC [DataWH].[motivation].[sp_PrepareSales];\n'+source[end:]
        for variant in ('main','all_2026','separate_2026'):
            cursor=Mock()
            def execute(sql,*args):
                if 'OBJECT_DEFINITION' in sql:
                    cursor.fetchone.return_value=(helper,) if "sp_PrepareSales" in sql else (definition,None)
                elif "name=N'Discount'" in sql:cursor.fetchone.return_value=(59,)
                else:cursor.fetchone.return_value=(0,)
                cursor.fetchall.return_value=[('SubdivisionKey',),('SubdivisionName',)]
                return cursor
            cursor.execute.side_effect=execute
            with self.subTest(variant=variant),patch('admin_motivation.services.calculation_jobs.rules',return_value=[]):
                prepare(cursor,variant)
            sql=cursor.execute.call_args.args[0]
            self.assertIn(sales.MARKER,sql)
            self.assertIn(sales.TABLE,sql)
            self.assertNotIn('SET s.DiscountMotivation',sql)
            self.assertNotIn('EXEC [DataWH].[motivation].[sp_PrepareSales]',sql)
