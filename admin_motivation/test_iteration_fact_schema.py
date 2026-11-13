"""Execute generated normalized CTE projections and verify their public schema."""
import re
import sqlite3
from django.test import SimpleTestCase
from .services.coefficient_iteration_2025 import build_sql


class IterationFactSchemaTests(SimpleTestCase):
    def projections(self):
        sql=build_sql()
        projections=[]
        for line in sql.splitlines():
            value=line.strip()
            if value.startswith(',COALESCE(NULLIF(LTRIM(RTRIM(p.PlanningGroupSalesERP))'):
                projections.append(value[1:])
        self.assertEqual(len(projections),2)
        return sql,projections

    def test_both_fact_branches_keep_named_fifth_column_and_blank_group_fallback(self):
        _,projections=self.projections()
        # These scalar functions have identical behavior in SQLite and T-SQL.
        adapt=lambda value:value.replace("N'","'")
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE Product(PlanningGroupSalesERP)')
            db.executemany('INSERT INTO Product VALUES (?)',[(None,),('',),('   ',),('1. RAC',),('9. OTHER',)])
            for expression in projections:
                cursor=db.execute('SELECT '+adapt(expression)+' FROM Product p')
                self.assertEqual(cursor.description[0][0],'PlanningGroupSalesERP')
                self.assertEqual([r[0] for r in cursor.fetchall()],['9. OTHER','9. OTHER','9. OTHER','1. RAC','9. OTHER'])
            query='WITH fact AS ('+' UNION ALL '.join('SELECT '+adapt(e)+' FROM Product p' for e in projections)+') SELECT PlanningGroupSalesERP FROM fact'
            self.assertEqual(len(db.execute(query).fetchall()),10)

    def test_explicit_column_names_do_not_leak_into_rate_predicates(self):
        sql,projections=self.projections()
        expression=projections[0].split(' AS ')[0]
        self.assertIn('gm.PGSales='+expression,sql)
        self.assertNotIn('gm.PGSales='+expression+' AS ',sql)
        self.assertEqual(len(re.findall(re.escape(expression)+r' AS PlanningGroupSalesERP',sql)),2)
        self.assertIn('n.SubdivisionKey=p.Subdivision',sql)
        self.assertIn('named.SubdivisionName=c.Subdivision',sql)
