"""Regression: SQL Server 8115 while loading recurring decimal author weights."""
from collections import defaultdict
from datetime import date
from decimal import Decimal,localcontext
from types import SimpleNamespace
from unittest.mock import Mock
from django.test import SimpleTestCase
from .services.shareholder_allocation import author_weights,split_amount
from .services.shareholder_runner import prepare_mapping


class SQLWeightPrecisionTests(SimpleTestCase):
    def test_three_recurring_shares_fit_sql_scale_and_keep_exact_residual(self):
        rows=author_weights([('Москва','A',100),('Москва','B',100),('Москва','C',100),('Москва','Small',1)],1)
        small=[row for row in rows if row['original']=='Small']
        self.assertEqual(len(small),3)
        self.assertTrue(all(row['weight'].as_tuple().exponent>=-24 for row in small))
        self.assertEqual(sum(row['weight'] for row in small),Decimal(1))
        self.assertEqual(small[0]['weight'],Decimal('0.333333333333333333333334'))
        self.assertEqual(sum(split_amount('-1234567.890123',small)),Decimal('-1234567.890123'))

    def test_odbc_receives_fixed_point_strings_instead_of_38_digit_fractions(self):
        cursor=Mock();cursor.fetchone.return_value=(0,)
        cursor.fetchall.return_value=[('Москва','A',400),('Москва','B',300),('Москва','Small',1)]
        prepare_mapping(cursor,SimpleNamespace(quarters=[1,2,3,4],threshold_percent=Decimal(1),as_of=date(2026,7,24)))
        statement,parameters=cursor.executemany.call_args.args
        self.assertEqual(statement,'INSERT INTO #share_author_map VALUES (?,?,?,?)')
        sums=defaultdict(Decimal)
        for branch,original,assigned,value in parameters:
            self.assertRegex(value,r'^[01]\.\d{24}$')
            self.assertLessEqual(len(value.replace('.','')),38)
            sums[branch,original]+=Decimal(value)
        self.assertTrue(all(value==1 for value in sums.values()))

    def test_small_values_and_unit_values_never_use_exponent_notation(self):
        cursor=Mock();cursor.fetchone.return_value=(0,)
        cursor.fetchall.return_value=[('Москва','A',Decimal('1e25')),('Москва','B',Decimal(1)),('Москва','',Decimal('0.01'))]
        prepare_mapping(cursor,SimpleNamespace(quarters=[1],threshold_percent=Decimal(0),as_of=date(2026,7,24)))
        for row in cursor.executemany.call_args.args[1]:
            self.assertNotIn('E',row[-1].upper())
            self.assertRegex(row[-1],r'^[01]\.\d{24}$')
