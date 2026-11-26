from datetime import date
from decimal import Decimal
from unittest.mock import Mock
import re,sqlite3
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from base.testing import authorize_admin
from .models import GlobalCoeffVersion
from .services import planning_group_rules as rules

RULE=dict(subdivision='Д-Питер',kind='Политики',planning='3. PROF',formula='Группа планов продаж',missing='minimum')
SOURCE="""SELECT COALESCE(sc.K0,0.001) AS Rate0,COALESCE(sc.K1,0.002) AS Rate1,
COALESCE(sc.VariationCalculate,N'Подразделение(Глобально)') AS VariationCalculate,
TRIM(sc.TypeCoeff) TypeCoeff_
FROM inputs s LEFT JOIN rates sc ON s.Date_ BETWEEN sc.DateFrom AND sc.DateTo
AND s.PGSales=sc.PGSales AND s.Subdivision=sc.Subdivision AND sc.TypeCoeff=N'Политики' AND s.Brand=sc.Brand;"""


class GroupRuleSQLTests(SimpleTestCase):
    def query(self,method='minimum',brand='Hydro',branch='Д-Питер',rates=True):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.create_function('CONVERT',2,lambda kind,value:float(value))
        db.executescript('''CREATE TABLE inputs(Date_,PGSales,Subdivision,Brand);
CREATE TABLE rates(DateFrom,DateTo,PGSales,Subdivision,TypeCoeff,Brand,K0,K1,VariationCalculate);
CREATE TABLE group_rules(Date_,Subdivision,TypeCoeff,PGSales,Formula,Missing,K0 REAL,K1 REAL,K2 REAL,K3 REAL,K4 REAL);''')
        db.execute('INSERT INTO inputs VALUES(?,?,?,?)',('2026-06-01','3. PROF',branch,brand))
        if rates:
            db.executemany('INSERT INTO rates VALUES(?,?,?,?,?,?,?,?,?)',[
                ('2026-01-01','2026-12-31','3. PROF','Д-Питер','Политики','Fan',.004,.009,'Подразделение'),
                ('2026-01-01','2026-12-31','3. PROF','Д-Питер','Политики','Chiller',.005,.008,'Подразделение'),
                ('2026-01-01','2026-12-31','3. PROF','Д-Москва','Политики','Hydro',.0001,.0001,'Подразделение')])
        row={**RULE,'missing':method,**{'k'+str(i):'.007' for i in range(5)}}
        db.executemany('INSERT INTO group_rules VALUES(?,?,?,?,?,?,?,?,?,?,?)',rules.tuples([dict(date='2026-01-01',rules=[row])]))
        sql=rules.patch(SOURCE,table='group_rules',minimum_source='rates')
        sql=re.sub(r'TOP\s*\(1\)\s*','',sql,flags=re.I).replace("N'","'").replace('CONVERT(float,',"CONVERT('float',").replace("'+gr.PGSales","'||gr.PGSales")
        return db,sql

    def test_new_prof_product_uses_independent_segment_minima_and_group_formula(self):
        db,sql=self.query()
        self.assertEqual(db.execute(sql).fetchone(),(.004,.008,'Группа планов продаж','Политики'))

    def test_exact_rates_survive_group_formula_override(self):
        db,sql=self.query(brand='Fan')
        self.assertEqual(db.execute(sql).fetchone(),(.004,.009,'Группа планов продаж','Политики'))

    def test_fixed_and_automatic_missing_rates_are_separate(self):
        for method,expected in [('fixed',(.007,.007)),('auto',(.001,.002))]:
            db,sql=self.query(method)
            self.assertEqual(db.execute(sql).fetchone()[:2],expected)

    def test_no_group_rates_and_explicit_error_fail_without_cross_branch_fallback(self):
        for method,rates in [('minimum',False),('error',True)]:
            db,sql=self.query(method,rates=rates)
            with self.assertRaises(sqlite3.OperationalError):db.execute(sql).fetchone()

    def test_unrelated_branch_keeps_native_formula_and_rates(self):
        db,sql=self.query(branch='Д-Москва')
        self.assertEqual(db.execute(sql).fetchone(),(.0001,.0001,'Подразделение','Политики'))

    def test_removing_rule_in_new_version_stops_inheritance(self):
        db,sql=self.query()
        db.execute("INSERT INTO group_rules VALUES('2026-05-01','','','',NULL,'auto',NULL,NULL,NULL,NULL,NULL)")
        self.assertEqual(db.execute(sql).fetchone(),(.001,.002,'Подразделение(Глобально)',None))

    def test_branch_rule_has_priority_over_common_rule(self):
        db,sql=self.query()
        db.execute("INSERT INTO group_rules VALUES('2026-01-01','','Политики','3. PROF','Подразделение','fixed',0.01,0.01,0.01,0.01,0.01)")
        self.assertEqual(db.execute(sql).fetchone()[:3],(.004,.008,'Группа планов продаж'))


class GroupRuleVersionTests(TestCase):
    def setUp(self):
        self.actor=authorize_admin(self.client)
        from .services.versions import create_version
        latest=GlobalCoeffVersion.objects.order_by('-pk').first()
        self.version=create_version(date(2026,1,1),source_version=latest.pk) if latest else GlobalCoeffVersion.objects.create(effective_from=date(2001,1,1))

    def test_save_copy_freeze_and_stale_revision(self):
        from .services.versions import create_version
        saved=rules.save(self.actor,self.version.pk,0,RULE)
        with self.assertRaises(ValidationError):rules.save(self.actor,saved.pk,0,RULE)
        snapshot=rules.frozen([saved]);copied=create_version(date(2027,1,1),source_version=saved.pk)
        self.assertEqual(copied.planning_rules,[RULE])
        copied.planning_rules[0]['missing']='error';copied.save()
        saved.refresh_from_db();self.assertEqual(saved.planning_rules,[RULE])
        self.assertEqual(snapshot[0]['rules'],[RULE])
        with self.assertRaises(ValidationError):rules.save(self.actor,saved.pk,saved.revision,RULE)

    def test_page_and_percent_input(self):
        from .models import Subdivision,Chanel
        channel,_=Chanel.objects.get_or_create(chanel_name='Канал')
        Subdivision.objects.get_or_create(subdivision_key='Д-Питер',defaults=dict(chanel=channel,subdivision_name='Д-Питер',subdivision_global='Д-Питер'))
        url=reverse('motivation_planning_group_rules')
        response=self.client.get(url,{'version':self.version.pk})
        self.assertContains(response,'Минимум внутри группы')
        data={**RULE,'version':self.version.pk,'revision':0,'missing':'fixed',**{'k'+str(i):'0.5' for i in range(5)}}
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        self.version.refresh_from_db();self.assertEqual(self.version.planning_rules[0]['k0'],'0.005')

    def test_all_types_save_together_replace_existing_and_preserve_other_groups(self):
        other={**RULE,'planning':'1. RAC'}
        self.version.planning_rules=[dict(RULE,subdivision='',kind='Продажи',missing='error'),other];self.version.save()
        response=self.client.post(reverse('motivation_planning_group_rules'),dict(RULE,subdivision='',kind='all',version=self.version.pk,revision=0))
        self.assertEqual(response.status_code,302)
        self.version.refresh_from_db()
        selected=[r for r in self.version.planning_rules if r['subdivision']=='' and r['planning']=='3. PROF']
        self.assertEqual({r['kind'] for r in selected},set(rules.KINDS))
        self.assertEqual(len(selected),3)
        self.assertTrue(all(r['missing']=='minimum' for r in selected))
        self.assertIn(other,self.version.planning_rules)
        self.assertEqual(self.version.revision,1)
        rules.validate(self.version.planning_rules)

    def test_compiler_supports_actual_procedure_and_private_frozen_rules(self):
        from .test_shareholder_regions import RegionalSQLTests
        from .services.shareholder_sql import private_sql
        compiled=RegionalSQLTests.compiled(self,'all_2026')
        patched=rules.patch(compiled)
        self.assertIn('gr.Formula',patched)
        private=private_sql(patched,'combined','groups',original_rules=True,regional_policy=True,planning_groups=True)
        self.assertIn('#share_group_rules',private)
        self.assertNotIn(rules.TABLE,private)
        self.assertIn('FROM #share_region_coefficients) grc',private)

    def test_approved_rules_are_immutable_and_enable_compiler(self):
        from django.db.models import QuerySet
        from .test_shareholder_regions import RegionalSQLTests
        self.version.planning_rules=[RULE];self.version.save()
        QuerySet.update(GlobalCoeffVersion.objects.filter(pk=self.version.pk),status='approved')
        self.version.refresh_from_db();self.version.planning_rules=[]
        with self.assertRaises(ValidationError):self.version.save()
        for variant in ['main','all_2026','separate_2026']:
            with self.subTest(variant=variant):
                compiled=RegionalSQLTests.compiled(self,variant)
                self.assertIn('__PORTAL_GROUP_RULES__',compiled)
