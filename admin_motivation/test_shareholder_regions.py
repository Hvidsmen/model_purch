from datetime import date,timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock,patch
import sqlite3,re
from django.test import TestCase,SimpleTestCase
from django.core.exceptions import ValidationError
from django.http import QueryDict
from .services import shareholder_regions as regions,shareholder_runner as runner,shareholder_reports as reports
from .services.shareholder_sql import private_sql,temporary_tables
from . import test_shareholder_original_rules as original_tests
from . import test_shareholders as fixtures
from .models import GlobalCoeffVersion,Subdivision,SubdivisionCoeff,Goods,TypeCoeff,SegmentCoeff,VariationCalculate,ShareholderSnapshot,Chanel


class RegionalSQLTests(TestCase):
    compiled=original_tests.OriginalSQLTests.compiled

    def test_full_real_pipeline_keeps_regions_independent_and_moscow_legacy(self):
        for variant,mode in [('all_2026','combined'),('separate_2026','separate')]:
            definition=self.compiled(variant)
            old=private_sql(definition,mode,'old',original_rules=True)
            sql=private_sql(definition,mode,'new',original_rules=True,regional_policy=True)
            self.assertNotIn('#share_region_plan',old)
            self.assertIn('WHERE SubdivisionName IN('+regions.BRANCH_SQL+')',sql)
            if mode=='combined':self.assertIn("UPDATE #share_subdivisions SET SubdivisionName=N'Д-Москва' WHERE SubdivisionName=N'ТЕРМОТРЕЙД'",sql)
            self.assertIn("THEN LTRIM(RTRIM(s.[Даичи_АвторСделкиНаименование])) ELSE ('Политики')",sql)
            self.assertIn('FROM #share_region_coefficients',sql)
            self.assertIn('FROM #share_region_defaults',sql)
            self.assertIn('COALESCE(sc.VariationCalculate,CASE WHEN',sql)
            self.assertIn("[АвторСделки] IN(N'Политики',N'Продажи')",sql)
            self.assertIn("s.SubdivisionName NOT IN ("+regions.REGION_SQL+") AND",sql)
            self.assertIn("p.[ТипКоэф]))=N'Продажи'",sql)
            self.assertIn('w.Period_',sql)
            self.assertIn('#share_plan_weights',temporary_tables(sql))
            self.assertIn('#share_region_department',temporary_tables(sql))
            self.assertLess(sql.index('INTO #share_region_department'),re.search(r"'1 Квартал'\s+КварталМотивации",sql).start())
            self.assertIn('СуммаМотивацииUSDГод',sql)
            self.assertNotIn('INTO DataWH.motivation.',sql)
            self.assertIn('THROW 50084',sql)
            # Annual eligibility includes all three gates, even for global mode.
            annual=sql[sql.index('SELECT DISTINCT [Подразделение]'):]
            self.assertIn('d.Period_=5',annual)
            self.assertIn('PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф',annual)
            self.assertIn("VariationCalculate=N'Марка(Бренд)'",annual)

    def test_installed_join_formatting_and_alternate_type_filters_are_supported(self):
        definition=self.compiled('all_2026')
        bracketed=re.sub(r'\bsc\.(\w+)',r'[sc].[\1]',definition)
        bracketed=bracketed.replace('TRIM([sc].[TypeCoeff])','LTRIM(RTRIM([sc].[TypeCoeff]))')
        single_in=re.sub(r"TRIM\(sc.TypeCoeff\)\s*=\s*'Политики'", "TRIM(sc.TypeCoeff) IN (N'Политики')",definition)
        for source in (bracketed,single_in):
            sql=private_sql(source,'combined','new',original_rules=True,regional_policy=True)
            self.assertGreaterEqual(sql.count('FROM #share_region_coefficients'),4)
            self.assertIn("OR LTRIM(RTRIM(TypeCoeff))<>N'Политики'",sql)
            self.assertIn('FROM #share_region_defaults',sql)
            self.assertIn('d.Period_=5',sql)
        self.assertIn('LTRIM(RTRIM([sc].[TypeCoeff]))',private_sql(bracketed,'combined','new',original_rules=True,regional_policy=True))

    def test_nested_select_before_type_predicate_does_not_hide_join(self):
        definition=self.compiled('all_2026')
        definition,count=re.subn(r'(vSubdivisionMotiveCoeffTo\s+sc\s+ON\s+)',r'\1 1=(SELECT 1) AND ',definition,count=1,flags=re.I)
        self.assertEqual(count,1)
        sql=private_sql(definition,'combined','new',original_rules=True,regional_policy=True)
        self.assertIn('1=(SELECT 1) AND',sql)
        self.assertIn('FROM #share_region_coefficients',sql)

    def test_one_qualified_reference_and_unqualified_fact_plan_joins(self):
        definition=self.compiled('all_2026')
        view='DataWH.motivation.vSubdivisionMotiveCoeffTo'
        self.assertEqual(definition.count(view),4)
        first=definition.index(view)+len(view)
        definition=definition[:first]+definition[first:].replace(view,'[vSubdivisionMotiveCoeffTo]')
        self.assertEqual(definition.count(view),1)
        sql=private_sql(definition,'combined','new',original_rules=True,regional_policy=True)
        self.assertEqual(sql.count('UNION ALL SELECT '+','.join(regions.FIELDS)+' FROM #share_region_coefficients'),4)
        self.assertIn('d.Period_=5',sql)

    def test_single_view_read_into_cache_still_patches_fact_and_plan(self):
        definition=self.compiled('all_2026')
        view='DataWH.motivation.vSubdivisionMotiveCoeffTo'
        definition=definition.replace(view,'#native_coeff')
        point=re.search(r';\s*WITH\s+fact\s+AS',definition,re.I).start()
        definition=definition[:point]+';SELECT sc.* INTO #native_coeff FROM '+view+' sc;\n'+definition[point:]
        self.assertEqual(definition.count(view),1)
        sql=private_sql(definition,'combined','new',original_rules=True,regional_policy=True)
        self.assertIn('SELECT sc.* INTO #native_coeff FROM '+view,sql)
        self.assertEqual(sql.count('FROM #native_coeff WHERE Subdivision NOT IN ('),4)
        self.assertEqual(sql.count('FROM #share_region_coefficients)'),4)
        self.assertIn('#native_coeff',temporary_tables(sql))

    def test_unknown_fact_or_plan_source_fails_even_with_multiple_view_mentions(self):
        definition=self.compiled('all_2026')
        view='DataWH.motivation.vSubdivisionMotiveCoeffTo'
        first_plan=definition.index('INTO #plan')
        for changed in (definition[:first_plan]+definition[first_plan:].replace(view,'#unknown_coeff'),
                        definition[:first_plan].replace(view,'#unknown_coeff')+definition[first_plan:]):
            with self.assertRaisesMessage(ValidationError,'соединения коэффициентов факта и плана'):
                private_sql(changed,'combined','new',original_rules=True,regional_policy=True)

    def test_cte_coefficient_provenance_and_unrelated_tables(self):
        source='WITH rates AS (SELECT * FROM motivation.vSubdivisionMotiveCoeffTo), other AS (SELECT * FROM unrelated) SELECT * FROM fact LEFT JOIN rates AS [sc] ON 1=1; SELECT * FROM plan LEFT JOIN other sc ON 1=1;'
        matches=regions.coefficient_joins(source)
        self.assertEqual([m['relation'] for m in matches],['rates'])

    def test_nested_unions_before_coefficient_joins_do_not_end_fact_or_plan(self):
        definition=self.compiled('all_2026')
        view='DataWH.motivation.vSubdivisionMotiveCoeffTo'
        # A derived query can combine input kinds before the outer rate JOIN.
        # Its UNION must not be mistaken for the end of the outer fact/plan.
        definition,count=re.subn(r'(LEFT\s+JOIN\s+'+re.escape(view)+r'\s+sc\s+ON)',
            r'CROSS JOIN (SELECT 1 AS Marker_ UNION ALL SELECT 2) shared_inputs \1',definition,flags=re.I)
        self.assertEqual(count,4)
        # Keep two policy joins recognizable and use native derived relations
        # for the other coefficient kind, matching the reported count of two.
        seen=0
        def native_sales(match):
            nonlocal seen
            seen+=1
            return '(SELECT * FROM '+view+') sc' if seen in (2,4) else match[0]
        definition=re.sub(re.escape(view)+r'\s+sc',native_sales,definition)
        self.assertEqual(len(regions.coefficient_joins(definition)),2)
        sql=private_sql(definition,'combined','new',original_rules=True,regional_policy=True)
        self.assertEqual(sql.count('shared_inputs LEFT JOIN'),4)
        self.assertEqual(sql.count('FROM #share_region_coefficients)'),2)
        self.assertIn('d.Period_=5',sql)

    def test_shared_fact_plan_default_projections_need_both_contexts_not_four_copies(self):
        source="SELECT COALESCE(sc.VariationCalculate,N'Подразделение') AS Formula FROM fact LEFT JOIN rates sc ON s.ClosedDealDate BETWEEN sc.DateFrom AND sc.DateTo; SELECT COALESCE(sc.VariationCalculate,N'Подразделение') AS Formula FROM plan LEFT JOIN rates sc ON t.Date_ BETWEEN sc.DateFrom AND sc.DateTo;"
        patched=regions.fallback_formulas(source)
        self.assertEqual(patched.count('FROM #share_region_defaults'),2)
        with self.assertRaisesMessage(ValidationError,'формулы факта и плана'):
            regions.fallback_formulas(source.replace('t.Date_', 's.ClosedDealDate'))

    def test_generated_relation_overrides_only_regional_policies(self):
        sql=private_sql(self.compiled('all_2026'),'combined','new',original_rules=True,regional_policy=True)
        relation=re.search(r'\(SELECT DateFrom,DateTo,Subdivision,[\s\S]*?FROM #share_region_coefficients\)',sql)[0]
        relation=relation.replace('DataWH.motivation.vSubdivisionMotiveCoeffTo','old_coeff').replace('#share_region_coefficients','frozen_coeff').replace("N'","'")
        db=sqlite3.connect(':memory:')
        columns=','.join(name+(' REAL' if name.startswith('K') else ' TEXT') for name in regions.FIELDS)
        for name in ('old_coeff','frozen_coeff'):db.execute('CREATE TABLE '+name+'('+columns+')')
        values=[('Д-Москва','Политики',.01),('Д-Ростов','Политики',.02),('Д-Ростов','Продажи',.03),('Д-Питер','Директора',.04),(None,'Политики',.05),('Д-Ростов',None,.06)]
        for table,entries in [('old_coeff',values),('frozen_coeff',[('Д-Ростов','Политики',.11),('Д-Питер','Политики',.12)])]:
            for branch,kind,rate in entries:
                fields=dict.fromkeys(regions.FIELDS,'')
                fields.update(Subdivision=branch,TypeCoeff=kind,K0=rate)
                db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in regions.FIELDS)+')',[fields[name] for name in regions.FIELDS])
        result={(branch,kind):rate for branch,kind,rate in db.execute('SELECT Subdivision,TypeCoeff,K0 FROM '+relation)}
        db.close()
        expected={(branch,kind):rate for branch,kind,rate in values}
        expected['Д-Ростов','Политики']=.11;expected['Д-Питер','Политики']=.12
        self.assertEqual(result,expected)

    def test_extra_employee_columns_and_comment_references_do_not_break_adapter(self):
        definition=self.compiled('all_2026')
        definition,count=re.subn(r"'Политики'\s+АвторСделки", "COALESCE(sc.EmployeeName,s.[Даичи_АвторСделкиНаименование]) AS АвторСделки",definition,count=1)
        self.assertEqual(count,1)
        definition+="\n-- DataWH.motivation.vSubdivisionMotiveCoeffTo sc WHERE TRIM(sc.TypeCoeff)='Политики'\n"
        sql=private_sql(definition,'combined','new',original_rules=True,regional_policy=True)
        self.assertIn('CAST(NULL AS nvarchar(255)) AS [EmployeeName]',sql)
        self.assertIn('ELSE (COALESCE(sc.EmployeeName,s.[Даичи_АвторСделкиНаименование]))',sql)
        self.assertEqual(sql.count('WHERE Subdivision NOT IN ('+regions.REGION_SQL+') OR '),4)

    def test_existing_moscow_author_expression_is_preserved(self):
        definition=self.compiled('all_2026')
        definition,count=re.subn(r"'Политики'\s+АвторСделки", "COALESCE(s.[Даичи_АвторСделкиНаименование],N'Политики') AS [АвторСделки]",definition,count=1)
        self.assertEqual(count,1)
        sql=private_sql(definition,'combined','new',original_rules=True,regional_policy=True)
        self.assertIn("ELSE (COALESCE(s.[Даичи_АвторСделкиНаименование],N'Политики')) END AS АвторСделки",sql)

    def test_missing_native_plan_or_author_shape_fails_closed(self):
        definition=self.compiled('all_2026')
        with self.assertRaises(ValidationError):private_sql(definition.replace('#percent_plan_menger','#renamed'), 'combined','new',original_rules=True,regional_policy=True)


class TripleGateTests(SimpleTestCase):
    def failures(self,department,first_plan,first_fact,second_plan,second_fact,formula='Марка(Бренд)'):
        db=sqlite3.connect(':memory:')
        db.executescript('''CREATE TABLE pf_base(Подразделение TEXT,Год INT,АвторСделки TEXT,ТипКоэф TEXT,ГруппаПлановПродаж TEXT,ГруппаТоваров TEXT,Марка TEXT,VariationCalculate TEXT,СуммаПланUSD REAL,СуммаОтгрузокUSD REAL);
 CREATE TABLE region_department(Подразделение TEXT,Год INT,Period_ INT,Execution_ REAL);''')
        for region in regions.REGIONS:
            db.execute('INSERT INTO region_department VALUES(?,2026,3,?)',(region,department))
            for brand,plan,fact in [('A',first_plan,first_fact),('B',second_plan,second_fact)]:
                db.execute('INSERT INTO pf_base VALUES(?,2026,?,?,?,?,?,?,?,?)',(region,'Автор','Политики','RAC','Goods',brand,formula,plan,fact))
        condition=regions.triple_failure('3').replace('#pf_base','pf_base').replace('#share_region_department','region_department').replace("N'","'")
        values=db.execute('SELECT Подразделение,Марка,'+condition+' AS Failed FROM pf_base',{'PayoutThreshold':.8}).fetchall()
        db.close();return {(r,b):failed for r,b,failed in values}

    def test_department_failure_blocks_even_when_employee_and_formula_pass(self):
        values=self.failures(.7,50,50,50,50)
        self.assertTrue(all(values.values()))

    def test_employee_failure_blocks_a_passing_brand(self):
        values=self.failures(.9,10,10,90,0)
        self.assertTrue(all(values[r,'A'] for r in regions.REGIONS))

    def test_brand_failure_blocks_even_when_employee_total_passes(self):
        values=self.failures(.9,50,10,50,90)
        for r in regions.REGIONS:self.assertEqual((values[r,'A'],values[r,'B']),(1,0))

    def test_global_formula_still_requires_employee_total(self):
        self.assertTrue(all(self.failures(.9,50,10,50,10,'Подразделение(Глобально)').values()))

    def test_every_gate_must_pass_and_q1_has_no_gate(self):
        self.assertFalse(any(self.failures(.9,50,50,50,50).values()))
        with self.assertRaises(ValueError):regions.triple_failure(1)


class RegionalRatesTests(TestCase):
    def make_rates(self):
        latest=GlobalCoeffVersion.objects.order_by('-effective_from').first()
        version=GlobalCoeffVersion.objects.create(effective_from=max(date(2026,1,1),latest.effective_from+timedelta(days=1)) if latest else date(2001,1,1))
        channel=Chanel.objects.create(chanel_name='Тест')
        kind=TypeCoeff.objects.create(type_coeff_name='Политики');formula=VariationCalculate.objects.create(variation_name='Марка(Бренд)')
        goods=Goods.objects.create(goods_key='RAC-A',planning_group_sales='1. RAC',group='Бытовые кондиционеры',brand='A')
        for name in regions.REGIONS:
            sub=Subdivision.all_objects.create(subdivision_key=name,subdivision_name=name,subdivision_global=name,chanel=channel)
            for i in range(5):
                segment,_=SegmentCoeff.objects.get_or_create(segment_name='K'+str(i))
                SubdivisionCoeff.objects.create(version=version,subdivision=sub,goods=goods,type_coeff=kind,segment=segment,motivation_coeff=.001*(i+1),manager_coeff=1,variation_calculate=formula)
        from django.db import models
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=version.pk),status='approved')
        version.refresh_from_db();return version

    def test_global_fallback_is_frozen_and_missing_exact_matrix_is_allowed(self):
        self.make_rates();saved=regions.approved_rates()
        self.assertEqual(saved['defaults'][0]['DefaultFormula'],'Подразделение(Глобально)')
        saved['coefficients']=[]
        cursor=Mock();regions.install_rates(cursor,SimpleNamespace(configuration={'regional_rates':saved}))
        self.assertIn('#share_region_defaults',cursor.executemany.call_args.args[0])
        self.assertEqual(cursor.executemany.call_args.args[1][0][1],'Подразделение(Глобально)')

    def test_only_approved_own_rates_are_frozen_and_missing_cells_are_rejected(self):
        version=self.make_rates();saved=regions.approved_rates()
        self.assertEqual({r['Subdivision'] for r in saved['coefficients']},set(regions.REGIONS))
        self.assertEqual(saved['coefficients'][0]['K1'],'0.002')
        self.assertEqual(saved['coefficients'][0]['VariationCalculate'],'Марка(Бренд)')
        cursor=Mock();run=SimpleNamespace(configuration={'regional_rates':saved})
        regions.install_rates(cursor,run)
        self.assertEqual(len(cursor.executemany.call_args.args[1]),2)
        self.assertTrue(cursor.executemany.call_args.args[1][0][1].endswith('T23:59:59.9999999'))
        self.assertEqual(saved,regions.approved_rates())
        # A blank snapshot must not substitute SQL's literal fallback rates.
        with self.assertRaises(ValidationError):regions.install_rates(cursor,SimpleNamespace(configuration={}))


class RegionalSnapshotTests(TestCase):
    setUp=fixtures.SnapshotTests.setUp
    url=fixtures.SnapshotTests.url

    def test_mapping_uses_whole_2026_in_regions_and_keeps_moscow_quarter_selection(self):
        self.run.configuration['regional_rule']=regions.RULE;self.run.quarters=[3]
        cursor=Mock();cursor.fetchone.return_value=(0,)
        cursor.fetchall.return_value=[('Д-Ростов','A',900),('Д-Ростов','tiny',1),('Д-Питер','B',500),('Д-Москва','M',200)]
        weights=runner.prepare_mapping(cursor,self.run)
        sql=cursor.execute.call_args_list[1].args[0]
        self.assertIn('sub.SubdivisionName IN ('+regions.REGION_SQL+') OR DATEPART(QUARTER,s.ClosedDealDate) IN (3)',sql)
        self.assertEqual({(w['branch'],w['assigned']) for w in weights},{('Д-Ростов','A'),('Д-Питер','B'),('Д-Москва','M')})

    def test_deal_filter_keeps_rostov_and_piter_in_combined_mode(self):
        self.run.configuration['regional_rule']=regions.RULE;self.run.save(update_fields=['configuration'])
        for i,branch in enumerate(regions.REGIONS):
            ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='deals',sequence=i,payload=[{'SourceBranch':branch,'ClosedDate':'2026-04-05','AssignedAuthor':'A'}])
        self.assertEqual([r['SourceBranch'] for r in reports.audit_rows(self.run,QueryDict('subdivision=Д-Ростов'))],['Д-Ростов'])
        self.assertEqual([r['SourceBranch'] for r in reports.audit_rows(self.run,QueryDict('subdivision=Д-Питер'))],['Д-Питер'])

    def test_all_new_reports_and_exports_keep_regional_totals_and_catchups_separate(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from .services.shareholder_original_rules import RULE
        self.run.configuration.update(rule=RULE,regional_rule=regions.RULE)
        self.run.save(update_fields=['configuration']);self.run.chunks.all().delete()
        for i,branch in enumerate(regions.REGIONS):
            paid='25' if i==0 else '0'
            data=[original_tests.row('3','100','0',branch=branch),original_tests.row('5','25',paid,branch=branch)]
            for dataset,payload in [('report_pol',data),('eligibility_pol',[original_tests.gate(data[-1],i==0)]),
                ('annual_pol',[dict(data[0],Квартал=3,AnnualUSD='25',AnnualRUB='2250')]),
                ('policy_plan_shares',[{'Subdivision':branch,'Author_':'Иванов','Turnover_':'1000','Weight_':'1'}]),
                ('policy_rates',[dict(Subdivision=branch,DateFrom='2026-01-01',DateTo='9999-12-31',PGSales='1. RAC',Group_='Goods',Brand='A',TypeCoeff='Политики',VariationCalculate='Марка(Бренд)',**{f'K{k}':'.001' for k in range(5)})])]:
                ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset=dataset,sequence=i,payload=runner.serial(payload))
        for branch,expected in [('Д-Ростов',125),('Д-Питер',0)]:
            query={'subdivision':branch,'author':'Иванов','type':'Политики'}
            for kind in ('team','mine','detail','registry','executive'):
                response=self.client.get(self.url(kind),query)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.context['ledger']['total'],expected)
                self.assertEqual({r['subdivision'] for r in response.context['ledger']['rows']},{branch})
                export=self.client.get(self.url(kind),{**query,'export':'xlsx'})
                self.assertEqual(export.status_code,200)
                book=load_workbook(BytesIO(b''.join(export.streaming_content)),data_only=True)
                sheet=book['Доплаты и годовая часть']
                self.assertEqual(sum(r[10] for r in list(sheet.values)[1:]),expected)
                self.assertEqual(book['Доли плана регионов'].cell(2,1).value,branch)
                self.assertEqual(book['Коэффициенты регионов'].cell(2,1).value,branch)
                book.close();export.close()

    def test_new_run_freezes_regional_rates_at_creation(self):
        saved={'versions':[{'id':4,'from':'2026-01-01','revision':10}],'coefficients':[{'Subdivision':'Д-Ростов','K0':'.001'}]}
        with patch.object(regions,'approved_rates',return_value=saved):
            run=runner.create({'source_job':self.job,'name':'Regions','as_of':date(2026,11,20),'scenario':'combined','quarters':['1','2','3','4'],'threshold_percent':Decimal(1)},self.user)
        saved['coefficients'][0]['K0']='.9'
        run.refresh_from_db()
        self.assertEqual(run.configuration['regional_rule'],regions.RULE)
        self.assertEqual(run.configuration['regional_rates']['coefficients'][0]['K0'],'.001')
