from datetime import date
from decimal import Decimal as D
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import patch, Mock
from django.test import TestCase, SimpleTestCase
from django.db.models import QuerySet
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.urls import reverse
from django.contrib.auth.models import User, Group
from openpyxl import load_workbook
from base.testing import authorize_test_case
from .models import (SalesPlanScenario,SalesPlanLine,GlobalCoeffVersion,SubdivisionDirectorCoeff,
    DirectorEmployee,Chanel,Subdivision,Goods,SegmentCoeff,VariationCalculate,
    DirectorScenarioComparison,KindManagerCoeff,SubdivisionManagerCoeff)
from .services.director_scenarios import calculate, director_result, BRANCHES, Rates
from .services.director_scenario_inputs import read_sources, plan_inputs
from .services.recalculation_2025 import RULE_VERSION as SOURCE_RULE
from .services.director_scenario_excel import workbook


def fact(branch,quarter,amount,**extra):
    return dict(branch=branch,quarter=quarter,planning='1. RAC',goods='Бытовые кондиционеры',brand='Brand',
        segment='O0',positive=str(max(D(amount),0)),negative=str(min(D(amount),0)),**extra)


class DirectorScenarioTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.version=GlobalCoeffVersion.objects.get(effective_from=date(2001,1,1))
        channel,_=Chanel.objects.get_or_create(chanel_name='Dealer')
        self.branches=[Subdivision.objects.create(subdivision_key=b,subdivision_name=b,subdivision_global=b,chanel=channel) for b in BRANCHES]
        self.good=Goods.objects.create(goods_key='G',planning_group_sales='1. RAC',group='Бытовые кондиционеры',brand='Brand')
        self.formula,_=VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')
        self.people=[DirectorEmployee.objects.create(full_name=f'Actor {i}') for i in range(4)]
        for index,person in enumerate(self.people):
            sub=self.branches[index%2]
            person.subdivisions.add(sub)
            rate='.01' if index==0 else '.02' if index==1 else '.9'
            SubdivisionDirectorCoeff.objects.create(version=self.version,employee=person,subdivision=sub,
                goods=self.good,variation_calculate=self.formula,**{f'k{i}':rate for i in range(5)})
        self.plans={year:SalesPlanScenario.objects.create(title=f'Plan {year}',source_version=f'P{year}',loaded_at=timezone.now()) for year in (2025,2026)}
        for year,plan in self.plans.items():
            for branch in BRANCHES:
                for q in range(1,5):self.line(plan,branch,1000,date(year,q*3-2,1))
        self.facts={year:[fact(b,q,'500' if i==0 else '1500') for i,b in enumerate(BRANCHES) for q in range(1,5 if year==2025 else 4)] for year in (2025,2026)}
        self.sources={str(year):{'run_id':f'run{year}','completed_at':'2026-11-10','parameters':
            {'fact_year':year,'plan_version':f'P{year}','quarter_share':'80','year_share':'20','payout_threshold':'.8','recalculation_rule':SOURCE_RULE}} for year in (2025,2026)}


    def line(self,plan,branch,amount,day,brand="Brand"):
        return SalesPlanLine.objects.create(scenario=plan,subdivision=branch,plan_date=day,
            planning_group_sales='1. RAC',group='Бытовые кондиционеры',brand=brand,amount_usd=amount,
            segment_amounts={f'O{i}':str(amount) if i==0 else '0' for i in range(5)})

    def approve(self):QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')

    def run_calc(self):
        self.approve()
        with patch('admin_motivation.services.director_scenarios.read_sources',return_value=(self.facts,self.sources)):
            return calculate(self.plans[2025].pk,self.plans[2026].pk,self.version.pk,*[p.pk for p in self.people])

    def row(self,data,case,person=0):
        return next(r for r in data['rows'] if r['scenario']==case and r['employee_id']==self.people[person if case!=1 else person+2].pk)

    def test_five_variants_only_recipient_pair_in_2025(self):
        data=self.run_calc().results
        self.assertEqual(len(data['scenarios']),5);self.assertEqual(len(data['rows']),10)
        r=self.row(data,1)
        self.assertEqual(r['employee'],self.people[2].full_name)
        self.assertEqual(D(r['total']),4) # copied .01, Q1 only, no annual payout
        case1=[r for r in data['rows'] if r['scenario']==1]
        self.assertEqual({r['employee_id'] for r in case1},{p.pk for p in self.people[2:]})
        self.assertTrue(all(r['role']=='Директор' for r in data['rows']))
        self.assertNotIn('manager_ledger',data['inputs'])
        for r in data['rows']:
            if r['scenario']>1:self.assertIn(r['employee_id'],[p.pk for p in self.people[:2]])

    def test_fact_equals_plan_replaces_money_not_only_threshold(self):
        data=self.run_calc().results
        self.assertEqual(D(self.row(data,2)['total']),4)
        self.assertEqual(D(self.row(data,3)['total']),24)
        self.assertEqual(D(self.row(data,3,1)['total']),48)
        for detail in data['details']:
            if detail['scenario'] in (3,5):self.assertEqual(D(detail['fact']),D(detail['plan']))

    def test_merged_recomputes_execution_and_each_director_has_all_turnover(self):
        data=self.run_calc().results
        self.assertEqual(D(self.row(data,4)['total']),48)
        self.assertEqual(D(self.row(data,4,1)['total']),96)
        self.assertEqual(D(self.row(data,5)['total']),48)
        self.assertNotEqual(D(self.row(data,4)['total']),D(self.row(data,2)['total'])+D(self.row(data,2,1)['total']))

    def test_2026_has_no_q4_or_annual_payment_but_shows_accrual(self):
        r=self.row(self.run_calc().results,3)
        self.assertEqual(D(r['quarters'][3]),0);self.assertEqual(D(r['annual_paid']),0)
        self.assertEqual(D(r['annual_accrued']),6);self.assertEqual(D(r['bonus']),0)

    def test_annual_catchup_when_year_recovers_but_q2_q3_failed(self):
        self.facts[2025]=[fact(b,q,[100,100,100,3700][q-1]) for b in BRANCHES for q in range(1,5)]
        r=self.row(self.run_calc().results,1)
        self.assertEqual([D(v) for v in r['quarters']],[D('.8'),0,0,D('29.6')])
        self.assertEqual(D(r['annual_paid']),8);self.assertEqual(D(r['bonus']),D('1.6'))
        self.assertEqual(D(r['total']),40)

    def test_snapshot_immutable_and_no_actual_roster_or_rates_change(self):
        before=list(SubdivisionDirectorCoeff.objects.values_list('pk','k0','employee_id'))
        bindings=list(DirectorEmployee.subdivisions.through.objects.values_list('directoremployee_id','subdivision_id'))
        saved=self.run_calc();original=saved.results
        self.plans[2026].lines.update(amount_usd=999)
        saved.refresh_from_db();self.assertEqual(saved.results,original)
        self.assertEqual(before,list(SubdivisionDirectorCoeff.objects.values_list('pk','k0','employee_id')))
        self.assertEqual(bindings,list(DirectorEmployee.subdivisions.through.objects.values_list('directoremployee_id','subdivision_id')))

    def test_period_correction_from_selected_model_applies_before_catchup(self):
        kind=KindManagerCoeff.objects.create(name='1 кв.')
        SubdivisionManagerCoeff.objects.create(version=self.version,subdivision=self.branches[0],kind=kind,coeff=2)
        self.assertEqual(D(self.row(self.run_calc().results,3)['total']),32)

    def test_missing_rates_fail_without_overwriting_previous_result(self):
        saved=self.run_calc()
        QuerySet(model=SubdivisionDirectorCoeff).filter(employee=self.people[0]).delete()
        with self.assertRaises(ValidationError):self.run_calc()
        self.assertEqual(DirectorScenarioComparison.objects.count(),1);saved.refresh_from_db()
        self.assertEqual(D(self.row(saved.results,3)['total']),24)

    def test_no_separate_plan_fails_instead_of_splitting_merged(self):
        self.plans[2026].lines.filter(subdivision=BRANCHES[1]).delete()
        with self.assertRaisesMessage(ValidationError,'раздельного'):self.run_calc()

    def test_bad_segments_or_draft_coefficients_fail(self):
        with self.assertRaisesMessage(ValidationError,'утверждённую'):
            calculate(self.plans[2025].pk,self.plans[2026].pk,self.version.pk,*[p.pk for p in self.people])
        self.plans[2026].lines.update(segment_amounts={'O0':'1'})
        with self.assertRaisesMessage(ValidationError,'O0–O4'):self.run_calc()

    def test_duplicate_roles_fail(self):
        self.approve()
        with self.assertRaisesMessage(ValidationError,'разных'):
            calculate(self.plans[2025].pk,self.plans[2026].pk,self.version.pk,*([self.people[0].pk]*4))

    def test_excel_all_cases_execution_percentage_and_literal_names(self):
        self.people[0].full_name='=1+1';self.people[0].save()
        saved=self.run_calc();book=load_workbook(workbook(saved))
        self.assertEqual(book.sheetnames,['Сравнение',*[f'{i} Вариант' for i in range(1,6)],'Выполнение','Ставки','Параметры'])
        self.assertEqual(book['Сравнение'].max_row,11)
        self.assertEqual(book['1 Вариант'].max_row,3)
        self.assertEqual(book['3 Вариант'].max_row,3)
        self.assertEqual(book['3 Вариант']['J2'].value,None)
        self.assertEqual(book['3 Вариант']['D2'].value,'=1+1')
        self.assertEqual(book['3 Вариант']['D2'].data_type,'s')
        self.assertEqual(book['Выполнение']['L2'].number_format,'0.00%')
        self.assertEqual(book['Выполнение']['L2'].value,.5)
        self.assertEqual(book['3 Вариант']['N2'].value,24)

    def test_excel_parameters_has_no_selections_in_nonexistent_panes(self):
        from zipfile import ZipFile
        from xml.etree import ElementTree as ET
        raw=workbook(self.run_calc())
        with ZipFile(raw) as archive:
            root=ET.fromstring(archive.read('xl/worksheets/sheet9.xml'))
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        pane=root.find('.//s:pane',ns)
        self.assertEqual(pane.get('topLeftCell'),'A2')
        self.assertIsNone(pane.get('xSplit'))
        self.assertEqual(pane.get('ySplit'),'1')
        self.assertEqual([s.get('pane') for s in root.findall('.//s:selection',ns)],['bottomLeft'])

    def test_view_prg_persisted_result_and_export(self):
        self.approve();url=reverse('motivation_report_director_scenarios')
        payload={'plan_2025':self.plans[2025].pk,'plan_2026':self.plans[2026].pk,'coefficient_version':self.version.pk,
            **dict(zip(('source_moscow','source_tt','target_moscow','target_tt'),[p.pk for p in self.people]))}
        with patch('admin_motivation.services.director_scenarios.read_sources',return_value=(self.facts,self.sources)):
            response=self.client.post(url,payload)
        self.assertEqual(response.status_code,302)
        page=self.client.get(response.url);self.assertContains(page,'Скачать Excel')
        self.assertContains(page,'Actor 0');self.assertNotContains(page,'Обязательное поле.')
        download=self.client.get(response.url+'&export=xlsx')
        book=load_workbook(BytesIO(b''.join(download.streaming_content)));self.assertEqual(len(book.sheetnames),9)
        self.assertEqual(DirectorScenarioComparison.objects.count(),1)

    def test_real_reader_2026_uses_prepared_fact_and_unexecuted_main_settings(self):
        from .models import MotivationCalculationSchedule,DiscountSegment
        DiscountSegment.objects.create(version=self.version,type_discount='MASS',o1=10,o2=20,o3=30,o4=40)
        job=MotivationCalculationSchedule.objects.create(name='Main settings',variant='main',fact_year=2026,
            plan_version='P2026',quarter_share=65,year_share=35,payout_threshold='.8')
        self.approve()
        connection=Mock();cursor=Mock()
        cursor.fetchone.side_effect=[(0,),(1,),('r25','completed',json.dumps(self.sources['2025']['parameters']),'date'),
            (1,1,1),('prepared26','date')]
        def fact_rows(year):
            return [(r['branch'],r['quarter'],r['planning'],r['goods'],r['brand'],r['segment'],
                     D(r['positive']),D(r['negative']),0 if year==2025 else 1) for r in self.facts[year]]
        cursor.fetchall.side_effect=[fact_rows(2025),fact_rows(2026)]
        with patch('admin_motivation.services.director_scenario_inputs.require_columns'),patch('admin_motivation.services.director_prepared_fact.require_columns'):
            saved=calculate(self.plans[2025].pk,self.plans[2026].pk,self.version.pk,
                *[p.pk for p in self.people],connector=Mock(return_value=(connection,cursor)))
        source=saved.results['sources']['2026']
        self.assertEqual(source['run_id'],'prepared26')
        self.assertEqual(source['parameters']['settings_job_id'],job.pk)
        self.assertEqual(D(self.row(saved.results,3)['total']),D('19.5'))
        sql='\n'.join(c.args[0] for c in cursor.execute.call_args_list)
        self.assertNotIn('FactAll2026',sql)
        self.assertNotIn('ReportMotivationByDealAuthor',sql)
        self.assertNotIn('all_2026',[arg for c in cursor.execute.call_args_list for arg in c.args])
        connection.close.assert_called_once()
        params=list(load_workbook(workbook(saved))['Параметры'].values)
        self.assertTrue(any('settings_job_key' in str(row) and str(job.key) in str(row) for row in params))
        self.assertTrue(any('Подготовленный факт продаж' in str(row) for row in params))
        self.assertTrue(any('сегменты MASS' in str(row) for row in params))
        self.assertFalse(any('Менеджеры 2025' in str(row) for row in params))
        job.quarter_share=80;job.year_share=20;job.save()
        saved.refresh_from_db()
        self.assertEqual(D(saved.results['sources']['2026']['parameters']['quarter_share']),65)

    def test_previous_snapshot_labelled_without_rewriting_history(self):
        saved=self.run_calc()
        saved.results['rule']='director-comparison-five-v1'
        saved.save(update_fields=['results'])
        response=self.client.get(reverse('motivation_report_director_scenarios'))
        self.assertContains(response,'Это результат по прежним правилам')
        saved.refresh_from_db()
        self.assertEqual(saved.results['rule'],'director-comparison-five-v1')

    def test_global_roles_and_branch_scope(self):
        from base.access import FINANCIER,MODELER,DIRECTOR,MOTIVATION_VIEWER,BRANCH_DIRECTOR
        self.run_calc();url=reverse('motivation_report_director_scenarios')
        for role in (FINANCIER,MODELER,DIRECTOR,MOTIVATION_VIEWER,BRANCH_DIRECTOR):
            user=User.objects.create_user('comparison-'+role);user.groups.add(Group.objects.get(name=role));self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code,403 if role==BRANCH_DIRECTOR else 200)
            response=self.client.post(url,{})
            self.assertEqual(response.status_code,403 if role in (BRANCH_DIRECTOR,MOTIVATION_VIEWER) else 200)

    def test_zero_plan_does_not_fabricate_execution(self):
        self.plans[2026].lines.update(amount_usd=0,segment_amounts={f'O{i}':'0' for i in range(5)})
        saved=self.run_calc()
        items=[r for r in saved.results['details'] if r['scenario']==2 and r['period']=='2 квартал']
        self.assertTrue(items);self.assertTrue(all(r['ratio'] is None and D(r['paid'])==0 for r in items))

    def test_returns_reduce_execution_but_negative_accrual_is_clipped(self):
        self.facts[2026]=[fact(b,q,1000) for b in BRANCHES for q in (1,2,3)]+[fact(BRANCHES[0],3,-500)]
        r=self.row(self.run_calc().results,2)
        self.assertEqual([D(v) for v in r['quarters']],[8,8,0,0])


    def test_segment_weights_and_minimum_fallback_are_explicit(self):
        QuerySet(model=SubdivisionDirectorCoeff).filter(employee=self.people[0]).update(k1='.03')
        for line in self.plans[2026].lines.filter(subdivision=BRANCHES[0]):
            line.segment_amounts={'O0':'250','O1':'750','O2':'0','O3':'0','O4':'0'};line.save()
        self.assertEqual(D(self.row(self.run_calc().results,3)['total']),60)

    def test_original_plans_stay_separate_when_rate_branches_are_merged(self):
        self.branches[1].merged_into=self.branches[0];self.branches[1].merge_policy='target';self.branches[1].save()
        data=self.run_calc().results
        self.assertEqual(D(self.row(data,2)['total']),4)
        self.assertEqual(D(self.row(data,2,1)['total']),72)
        self.assertEqual(D(self.row(data,4,1)['total']),96)

    def test_mixed_case_thermotrade_uses_merged_moscow_rates(self):
        self.branches[1].subdivision_key='Термотрейд'
        self.branches[1].merged_into=self.branches[0]
        self.branches[1].merge_policy='target'
        self.branches[1].save()
        QuerySet(model=SubdivisionDirectorCoeff).filter(employee=self.people[1]).update(subdivision=self.branches[0])
        data=self.run_calc().results
        self.assertEqual(D(self.row(data,2,1)['total']),72)
        self.assertEqual(D(self.row(data,4,1)['total']),96)

    def test_non_global_goods_scope_and_department_gate(self):
        formula,_=VariationCalculate.objects.get_or_create(variation_name='Марка(Бренд)')
        QuerySet(model=SubdivisionDirectorCoeff).update(variation_calculate=formula)
        other=Goods.objects.create(goods_key='Second',planning_group_sales='1. RAC',group='Бытовые кондиционеры',brand='Other')
        for index,person in enumerate(self.people[:2]):
            SubdivisionDirectorCoeff.objects.create(version=self.version,employee=person,subdivision=self.branches[index],
                goods=other,variation_calculate=formula,**{f'k{i}':'.01' for i in range(5)})
        for plan in self.plans.values():
            for q in (1,2,3):
                line=self.line(plan,BRANCHES[0],9000,date(2025 if plan==self.plans[2025] else 2026,q*3-2,1),brand='Other')
        data=self.run_calc().results
        matching=[r for r in data['details'] if r['scenario']==2 and r['employee']==self.people[0].full_name and r['period']=='2 квартал' and r['brand']=='Brand']
        self.assertEqual(len(matching),1)
        self.assertEqual(D(matching[0]['department_ratio']),D('.05'))
        self.assertEqual(D(matching[0]['paid']),0)

    def test_empty_model_brand_is_an_exact_canonical_rate_not_group_minimum(self):
        mobile=Goods.objects.create(goods_key='Mobile blank',planning_group_sales='1. RAC',group='Мобильные кондиционеры',brand='')
        SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.branches[0],employee=self.people[0],
            goods=mobile,variation_calculate=self.formula,**{f'k{i}':'.03' for i in range(5)})
        rates=Rates(self.version)
        values,formula,origin=rates.get(self.people[0],BRANCHES[0],{'planning':'1. RAC','goods':'Мобильные кондиционеры','brand':'*'})
        self.assertEqual(values,[D('.03')]*5);self.assertEqual(origin,'exact')
        self.assertEqual(formula,'Подразделение(Глобально)')

    def test_conflicting_empty_and_star_model_cells_are_rejected_before_a_minimum(self):
        for brand,rate in [('', '.03'),('*','.04')]:
            good=Goods.objects.create(goods_key='Mobile '+repr(brand),planning_group_sales='1. RAC',group='Мобильные кондиционеры',brand=brand)
            SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.branches[0],employee=self.people[0],
                goods=good,variation_calculate=self.formula,**{f'k{i}':rate for i in range(5)})
        with self.assertRaisesMessage(ValidationError,'Неоднозначные ставки'):
            Rates(self.version).get(self.people[0],BRANCHES[0],{'planning':'1. RAC','goods':'Мобильные кондиционеры','brand':'*'})

    def test_report_catalog_contains_comparison(self):
        self.assertContains(self.client.get(reverse('motivation_reports')),reverse('motivation_report_director_scenarios'))


    def test_both_plan_selectors_probe_index_and_filter_year_and_loaded_state(self):
        from .reports_director_scenarios import ComparisonForm
        from django.db import connection
        for title,year,sub,loaded,deleted in [
            ('Company total',2026,'',True,False),('Unloaded',2025,BRANCHES[0],False,False),
            ('Deleted',2026,BRANCHES[0],True,True),('Other year',2024,BRANCHES[0],True,False),
        ]:
            plan=SalesPlanScenario.objects.create(title=title,source_version=title,is_deleted=deleted,
                loaded_at=timezone.now() if loaded else None)
            self.line(plan,sub,1,date(year,1,1))
        form=ComparisonForm()
        for year in (2025,2026):
            field=form.fields[f'plan_{year}']
            self.assertEqual(list(field.queryset.values_list('pk',flat=True)),[self.plans[year].pk])
            if connection.vendor=='sqlite':
                query_plan=field.queryset.explain()
                self.assertNotIn('SCAN U0',query_plan)
                self.assertIn('scenario_id=? AND plan_date>? AND plan_date<?',query_plan)
            with self.assertNumQueries(2):choices=list(field.choices)
            self.assertEqual(choices[-1][1],f'Plan {year} · P{year}')

    def test_opening_comparison_never_reads_dwh_or_calculates(self):
        with patch('admin_motivation.reports_director_scenarios.calculate') as calculator, \
             patch('admin_motivation.services.director_scenarios.read_sources') as reader:
            response=self.client.get(reverse('motivation_report_director_scenarios'))
        self.assertContains(response,'Подготовить сравнение')
        calculator.assert_not_called();reader.assert_not_called()


class SourceReadTests(SimpleTestCase):
    def setUp(self):
        self.conn=Mock();self.cursor=Mock();self.connector=Mock(return_value=(self.conn,self.cursor))
        self.plans={year:SimpleNamespace(source_version=f'P{year}') for year in (2025,2026)}
        params=lambda year:json.dumps({'fact_year':year,'plan_version':f'P{year}','quarter_share':'80','year_share':'20','payout_threshold':'.8','recalculation_rule':SOURCE_RULE})
        self.runs=[('r25','completed',params(2025),'today')]
        self.version=SimpleNamespace()
        schema=patch('admin_motivation.services.director_scenario_inputs.require_columns')
        schema.start();self.addCleanup(schema.stop)
        self.settings_patch=patch('admin_motivation.services.director_prepared_fact.settings_for',return_value={})
        self.prepared_patch=patch('admin_motivation.services.director_prepared_fact.read_fact',return_value=([],{}))
        self.settings_patch.start();self.prepared=self.prepared_patch.start()
        self.addCleanup(self.settings_patch.stop);self.addCleanup(self.prepared_patch.stop)
        self.cursor.fetchone.side_effect=[(0,),(1,),*self.runs]
        self.cursor.fetchall.side_effect=[[(BRANCHES[0],1,'1. RAC','AC','Brand','O0',100,-20,0)],
            [(BRANCHES[1],2,'1. RAC','AC','Brand','O1',200,0,0)],[]]

    def test_verified_sources_original_branch_and_read_only_lock(self):
        facts,metadata=read_sources(self.plans,self.version,self.connector)
        self.assertEqual(facts[2025][0]['negative'],'-20')
        sql='\n'.join(call.args[0] for call in self.cursor.execute.call_args_list)
        self.assertIn('s.SubdivisionKey=f.[ПодразделениеДокумент]',sql)
        self.assertIn("@LockMode=N'Shared'",sql)
        self.assertIn('f.[СуммаОтгрузокUSD]<>0',sql)
        self.assertNotIn('ReportMotivationByDealAuthorTest2025',sql)
        self.assertNotIn('АвторСделки',sql)
        self.assertNotIn('INSERT',sql)
        self.assertNotIn('all_2026',sql);self.assertNotIn('FactAll2026',sql)
        self.prepared.assert_called_once_with(self.cursor,self.version,BRANCHES,{})
        self.conn.close.assert_called_once()

    def test_failed_latest_run_rejected_and_connection_closed(self):
        self.cursor.fetchone.side_effect=[(0,),(1,),('r','failed','{}','today')]
        with self.assertRaisesMessage(ValidationError,'завершённого'):read_sources(self.plans,self.version,self.connector)
        self.conn.close.assert_called_once()

    def test_different_plan_rejected(self):
        self.plans[2025].source_version='other'
        with self.assertRaisesMessage(ValidationError,'не совпадает'):read_sources(self.plans,self.version,self.connector)

    def test_no_original_branch_or_unknown_segment_rejected(self):
        self.cursor.fetchall.side_effect=[[(BRANCHES[0],1,'PG','G','B','O0',100,0,1)]]
        with self.assertRaisesMessage(ValidationError,'исходный филиал'):read_sources(self.plans,self.version,self.connector)

    def test_busy_source_rejected(self):
        self.cursor.fetchone.side_effect=[(-1,)]
        with self.assertRaisesMessage(ValidationError,'выполняется'):read_sources(self.plans,self.version,self.connector)


import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1','Optional Chromium check')
class DirectorScenarioBrowserTests(StaticLiveServerTestCase):
    line=DirectorScenarioTests.line

    def test_all_five_variants_reload_and_excel(self):
        from playwright.sync_api import sync_playwright,expect
        from django.conf import settings
        from pathlib import Path
        DirectorScenarioTests.setUp(self)
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        with patch('admin_motivation.services.director_scenarios.read_sources',return_value=(self.facts,self.sources)):
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True)
                page=browser.new_page(viewport={'width':1600,'height':1100})
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                page.goto(self.live_server_url+reverse('motivation_reports'))
                page.get_by_role('link',name='Сравнение директорских сценариев · 2025 / 2026',exact=True).click()
                expect(page.get_by_role('heading',name='Сравнение директорских сценариев',exact=True)).to_be_visible()
                for year in (2025,2026):page.locator(f'select[name=plan_{year}]').select_option(str(self.plans[year].pk))
                page.locator('select[name=coefficient_version]').select_option(str(self.version.pk))
                from .reports_director_scenarios import ComparisonForm
                for key,person in zip(('source_moscow','source_tt','target_moscow','target_tt'),self.people):
                    label=ComparisonForm.base_fields[key].label
                    page.get_by_role('button',name=label,exact=True).click()
                    search=page.get_by_role('searchbox',name=f'Поиск: {label}',exact=True)
                    search.fill('No matching employee')
                    expect(page.locator('.searchable-filter-menu:visible')).to_contain_text('Нет совпадений')
                    search.fill(person.full_name)
                    page.get_by_role('button',name=f'{person.full_name} · № {person.pk}',exact=True).click()
                page.get_by_role('button',name='Рассчитать пять вариантов и сохранить',exact=True).click()
                expect(page.locator('#scenario-5')).to_contain_text('факт = план')
                expect(page.locator('table').nth(2)).to_contain_text('24.00')
                page.reload()
                expect(page.locator('#scenario-1')).to_contain_text('2025')
                with page.expect_download() as pending:page.get_by_role('link',name='Скачать Excel · все пять вариантов',exact=True).click()
                book=load_workbook(BytesIO(Path(pending.value.path()).read_bytes()))
                self.assertEqual(book['3 Вариант']['N2'].value,24)
                self.assertEqual(book['4 Вариант']['N2'].value,48)
                self.assertEqual(book['1 Вариант'].max_row,3)
                page.screenshot(path='/tmp/director-scenarios.png',full_page=True)
                browser.close()
