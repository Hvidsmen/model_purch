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
from .services.director_scenarios import calculate, director_result, manager_result, BRANCHES, Rates
from .services.director_scenario_inputs import read_sources, plan_inputs
from .services.director_scenario_excel import workbook


def fact(branch,quarter,amount,**extra):
    return dict(branch=branch,quarter=quarter,planning='1. RAC',goods='AC',brand='Brand',
        segment='O0',positive=str(max(D(amount),0)),negative=str(min(D(amount),0)),**extra)


class DirectorScenarioTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.version=GlobalCoeffVersion.objects.get(effective_from=date(2001,1,1))
        channel,_=Chanel.objects.get_or_create(chanel_name='Dealer')
        self.branches=[Subdivision.objects.create(subdivision_key=b,subdivision_name=b,subdivision_global=b,chanel=channel) for b in BRANCHES]
        self.good=Goods.objects.create(goods_key='G',planning_group_sales='1. RAC',group='AC',brand='Brand')
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
            {'fact_year':year,'plan_version':f'P{year}','quarter_share':'80','year_share':'20','payout_threshold':'.8'}} for year in (2025,2026)}
        self.ledger=[{'branch':BRANCHES[0],'author':self.people[0].full_name,'period':'1 Квартал','possible':'12','paid':'12','bonus':'0'},
                     {'branch':BRANCHES[1],'author':self.people[1].full_name,'period':'Год','possible':'20','paid':'18','bonus':'3'}]

    def line(self,plan,branch,amount,day,brand="Brand"):
        return SalesPlanLine.objects.create(scenario=plan,subdivision=branch,plan_date=day,
            planning_group_sales='1. RAC',group='AC',brand=brand,amount_usd=amount,
            segment_amounts={f'O{i}':str(amount) if i==0 else '0' for i in range(5)})

    def approve(self):QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')

    def run_calc(self):
        self.approve()
        with patch('admin_motivation.services.director_scenarios.read_sources',return_value=(self.facts,self.ledger,self.sources)):
            return calculate(self.plans[2025].pk,self.plans[2026].pk,self.version.pk,*[p.pk for p in self.people])

    def row(self,data,case,person=0):
        return next(r for r in data['rows'] if r['scenario']==case and r['employee_id']==self.people[person if case!=1 else person+2].pk)

    def test_five_variants_copied_rates_and_manager_results(self):
        data=self.run_calc().results
        self.assertEqual(len(data['scenarios']),5);self.assertEqual(len(data['rows']),12)
        r=self.row(data,1)
        self.assertEqual(r['employee'],self.people[2].full_name)
        self.assertEqual(D(r['total']),4) # copied .01, Q1 only, no annual payout
        manager=next(r for r in data['rows'] if r['role']=='Менеджер' and r['employee_id']==self.people[0].pk)
        self.assertEqual(D(manager['total']),12)
        other=next(r for r in data['rows'] if r['role']=='Менеджер' and r['employee_id']==self.people[1].pk)
        self.assertEqual(D(other['total']),21)
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

    def test_missing_manager_is_unknown_not_zero(self):
        self.ledger=[]
        row=next(r for r in self.run_calc().results['rows'] if r['role']=='Менеджер')
        self.assertIsNone(row['total'])

    def test_excel_all_cases_execution_percentage_and_literal_names(self):
        self.people[0].full_name='=1+1';self.people[0].save()
        saved=self.run_calc();book=load_workbook(workbook(saved))
        self.assertEqual(book.sheetnames,['Сравнение',*[f'{i} Вариант' for i in range(1,6)],'Выполнение','Ставки','Параметры'])
        self.assertEqual(book['Сравнение'].max_row,13)
        self.assertEqual(book['1 Вариант'].max_row,5)
        self.assertEqual(book['3 Вариант'].max_row,3)
        self.assertEqual(book['3 Вариант']['J2'].value,None)
        self.assertEqual(book['3 Вариант']['D2'].value,'=1+1')
        self.assertEqual(book['3 Вариант']['D2'].data_type,'s')
        self.assertEqual(book['Выполнение']['L2'].number_format,'0.00%')
        self.assertEqual(book['Выполнение']['L2'].value,.5)
        self.assertEqual(book['3 Вариант']['N2'].value,24)

    def test_view_prg_persisted_result_and_export(self):
        self.approve();url=reverse('motivation_report_director_scenarios')
        payload={'plan_2025':self.plans[2025].pk,'plan_2026':self.plans[2026].pk,'coefficient_version':self.version.pk,
            **dict(zip(('source_moscow','source_tt','target_moscow','target_tt'),[p.pk for p in self.people]))}
        with patch('admin_motivation.services.director_scenarios.read_sources',return_value=(self.facts,self.ledger,self.sources)):
            response=self.client.post(url,payload)
        self.assertEqual(response.status_code,302)
        page=self.client.get(response.url);self.assertContains(page,'Скачать Excel')
        self.assertContains(page,'Actor 0');self.assertNotContains(page,'Обязательное поле.')
        download=self.client.get(response.url+'&export=xlsx')
        book=load_workbook(BytesIO(b''.join(download.streaming_content)));self.assertEqual(len(book.sheetnames),9)
        self.assertEqual(DirectorScenarioComparison.objects.count(),1)

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

    def test_non_global_goods_scope_and_department_gate(self):
        formula,_=VariationCalculate.objects.get_or_create(variation_name='Марка(Бренд)')
        QuerySet(model=SubdivisionDirectorCoeff).update(variation_calculate=formula)
        other=Goods.objects.create(goods_key='Second',planning_group_sales='1. RAC',group='AC',brand='Other')
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

    def test_report_catalog_contains_comparison(self):
        self.assertContains(self.client.get(reverse('motivation_reports')),reverse('motivation_report_director_scenarios'))


class SourceReadTests(SimpleTestCase):
    def setUp(self):
        self.conn=Mock();self.cursor=Mock();self.connector=Mock(return_value=(self.conn,self.cursor))
        self.plans={year:SimpleNamespace(source_version=f'P{year}') for year in (2025,2026)}
        params=lambda year:json.dumps({'fact_year':year,'plan_version':f'P{year}','quarter_share':'80','year_share':'20','payout_threshold':'.8'})
        self.runs=[('r25','completed',params(2025),'today'),('r26','completed',params(2026),'today')]
        self.cursor.fetchone.side_effect=[(0,),(1,),*self.runs]
        self.cursor.fetchall.side_effect=[[(BRANCHES[0],1,'1. RAC','AC','Brand','O0',100,-20,0)],
            [(BRANCHES[1],2,'1. RAC','AC','Brand','O1',200,0,0)],[]]

    def test_verified_sources_original_branch_and_read_only_lock(self):
        facts,ledger,metadata=read_sources(self.plans,self.connector)
        self.assertEqual(facts[2025][0]['negative'],'-20')
        sql='\n'.join(call.args[0] for call in self.cursor.execute.call_args_list)
        self.assertIn('s.SubdivisionKey=f.[ПодразделениеДокумент]',sql)
        self.assertIn("@LockMode=N'Shared'",sql)
        self.assertIn('f.[СуммаОтгрузокUSD]<>0',sql)
        self.assertNotIn('INSERT',sql);self.conn.close.assert_called_once()

    def test_failed_latest_run_rejected_and_connection_closed(self):
        self.cursor.fetchone.side_effect=[(0,),(1,),('r','failed','{}','today')]
        with self.assertRaisesMessage(ValidationError,'завершённый'):read_sources(self.plans,self.connector)
        self.conn.close.assert_called_once()

    def test_different_plan_rejected(self):
        self.plans[2025].source_version='other'
        with self.assertRaisesMessage(ValidationError,'не совпадает'):read_sources(self.plans,self.connector)

    def test_no_original_branch_or_unknown_segment_rejected(self):
        self.cursor.fetchall.side_effect=[[(BRANCHES[0],1,'PG','G','B','O0',100,0,1)]]
        with self.assertRaisesMessage(ValidationError,'исходный филиал'):read_sources(self.plans,self.connector)

    def test_busy_source_rejected(self):
        self.cursor.fetchone.side_effect=[(-1,)]
        with self.assertRaisesMessage(ValidationError,'выполняется'):read_sources(self.plans,self.connector)


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
        with patch('admin_motivation.services.director_scenarios.read_sources',return_value=(self.facts,self.ledger,self.sources)):
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True)
                page=browser.new_page(viewport={'width':1600,'height':1100})
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                page.goto(self.live_server_url+reverse('motivation_report_director_scenarios'))
                for year in (2025,2026):page.locator(f'select[name=plan_{year}]').select_option(str(self.plans[year].pk))
                page.locator('select[name=coefficient_version]').select_option(str(self.version.pk))
                for key,person in zip(('source_moscow','source_tt','target_moscow','target_tt'),self.people):page.locator(f'select[name={key}]').select_option(str(person.pk))
                page.get_by_role('button',name='Рассчитать пять вариантов и сохранить',exact=True).click()
                expect(page.locator('#scenario-5')).to_contain_text('факт = план')
                expect(page.locator('table').nth(2)).to_contain_text('24.00')
                page.reload()
                expect(page.locator('#scenario-1')).to_contain_text('2025')
                with page.expect_download() as pending:page.get_by_role('link',name='Скачать Excel · все пять вариантов',exact=True).click()
                book=load_workbook(BytesIO(Path(pending.value.path()).read_bytes()))
                self.assertEqual(book['3 Вариант']['N2'].value,24)
                self.assertEqual(book['4 Вариант']['N2'].value,48)
                self.assertEqual(book['1 Вариант'].max_row,5)
                page.screenshot(path='/tmp/director-scenarios.png',full_page=True)
                browser.close()
