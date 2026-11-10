from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.db.models import QuerySet
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.urls import reverse
from django.contrib.auth.models import User,Group
from openpyxl import load_workbook
from base.testing import authorize_test_case
from .models import (SalesPlanScenario,SalesPlanLine,GlobalCoeffVersion,SubdivisionCoeff,
    SubdivisionDirectorCoeff,DirectorEmployee,Chanel,Subdivision,Goods,TypeCoeff,SegmentCoeff,
    VariationCalculate,IntegralPlan2026Calculation)
from .services.integral_plan_2026 import calculate,workbook


class IntegralPlan2026Tests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.version=GlobalCoeffVersion.objects.get(effective_from=date(2001,1,1))
        self.channel,_=Chanel.objects.get_or_create(chanel_name='Dealer')
        self.a=Subdivision.objects.create(subdivision_key='A',subdivision_name='A',subdivision_global='A',chanel=self.channel)
        self.b=Subdivision.objects.create(subdivision_key='B',subdivision_name='B',subdivision_global='B',chanel=self.channel)
        self.good=Goods.objects.create(goods_key='G',planning_group_sales='1. RAC',group='AC',brand='Brand')
        self.types=[TypeCoeff.objects.get_or_create(type_coeff_name=n)[0] for n in ('Политики','Продажи')]
        self.segments=[SegmentCoeff.objects.get_or_create(segment_name=f'K{i}')[0] for i in range(5)]
        self.formula,_=VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')
        self.plan=SalesPlanScenario.objects.create(title='Plan 2026',source_version='P26',loaded_at=timezone.now())
        self.rates(self.a,self.good,[.01]*5,[.02]*5)
        self.rates(self.b,self.good,[.02]*5,[.03]*5)
        self.line('A',1000)
        self.line('B',9000)
        self.director('Director A',self.a,'.04')
        self.director('Director B',self.a,'.01')
        self.director('Director C',self.b,'.02')

    def rates(self,sub,good,policies,sales):
        for kind,values in zip(self.types,(policies,sales)):
            for seg,value in zip(self.segments,values):
                SubdivisionCoeff.objects.create(version=self.version,subdivision=sub,goods=good,
                    type_coeff=kind,segment=seg,motivation_coeff=value,manager_coeff=1,variation_calculate=self.formula)

    def line(self,sub,amount,day=date(2026,1,1),good=None,segments=None):
        good=good or self.good
        return SalesPlanLine.objects.create(scenario=self.plan,subdivision=sub,plan_date=day,
            planning_group_sales=good.planning_group_sales,group=good.group,brand=good.brand,
            amount_usd=amount,segment_amounts=segments or dict(zip([f'O{i}' for i in range(5)],[str(amount),'0','0','0','0'])))

    def director(self,name,sub,rate,archived=False,bound=True):
        person=DirectorEmployee.objects.create(full_name=name,is_archived=archived)
        if bound:person.subdivisions.add(sub)
        SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=sub,goods=self.good,
            employee=person,**{f'k{i}':rate for i in range(5)})
        return person

    def run_calc(self):
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        return calculate(self.plan.pk,self.version.pk)

    def test_two_coefficients_and_weighted_company_total(self):
        saved=self.run_calc();data=saved.results
        a,b=data['rows']
        self.assertEqual(Decimal(a['combined']),30)
        self.assertEqual(Decimal(a['combined_coefficient']),Decimal('.03'))
        self.assertEqual(Decimal(a['directors']),50)
        self.assertEqual(Decimal(a['directors_coefficient']),Decimal('.05'))
        self.assertEqual(a['director_count'],2)
        self.assertEqual(Decimal(b['combined_coefficient']),Decimal('.05'))
        self.assertEqual(Decimal(data['total']['plan']),10000)
        self.assertEqual(Decimal(data['total']['combined']),480)
        self.assertEqual(Decimal(data['total']['combined_coefficient']),Decimal('.048'))
        self.assertEqual(Decimal(data['total']['directors']),230)
        self.assertEqual(Decimal(data['total']['directors_coefficient']),Decimal('.023'))
        self.assertEqual(self.plan.lines.filter(total_usd__isnull=False).count(),0)

    def test_archived_and_unbound_directors_do_not_add_premium(self):
        self.director('Archived',self.a,'.9',archived=True)
        self.director('Unbound',self.a,'.9',bound=False)
        self.assertEqual(Decimal(self.run_calc().results['rows'][0]['directors']),50)

    def test_year_filter_and_global_plan_do_not_double_count(self):
        self.line('A',9999,date(2025,1,1));self.line('A',8888,date(2027,1,1));self.line('',10000)
        self.assertEqual(Decimal(self.run_calc().results['total']['plan']),10000)

    def test_segment_weights_are_used_instead_of_simple_average(self):
        second=Goods.objects.create(goods_key='Other',planning_group_sales='2. VRF',group='VRF',brand='X')
        self.rates(self.a,second,[.01,.02,.03,.04,.05],[.01,.02,.03,.04,.05])
        self.line('A',100,good=second,segments={'O0':'25','O1':'75','O2':'0','O3':'0','O4':'0'})
        data=self.run_calc().results
        self.assertEqual(Decimal(data['rows'][0]['combined']),Decimal('33.50'))

    def test_missing_director_rates_are_not_reported_as_zero(self):
        person=DirectorEmployee.objects.create(full_name='No rates');person.subdivisions.add(self.a)
        row=self.run_calc().results['rows'][0]
        self.assertIsNone(row['directors_coefficient'])
        self.assertIsNone(row['directors'])
        self.assertTrue(any('Нет коэффициентов директора' in n for n in row['notes']))
        self.assertIsNotNone(row['combined_coefficient'])

    def test_heat_and_dpp_are_in_sales_and_directors_not_policies(self):
        heat=Goods.objects.create(goods_key='Heat',planning_group_sales='5. HEAT',group='*',brand='*')
        self.rates(self.a,heat,[.10]*5,[.02]*5);self.line('A',100,good=heat)
        channel,_=Chanel.objects.get_or_create(chanel_name='ДПП');self.b.chanel=channel;self.b.save()
        rows=self.run_calc().results['rows']
        self.assertEqual(Decimal(rows[0]['combined']),32)
        self.assertEqual(Decimal(rows[1]['combined']),270)
        self.assertEqual(Decimal(rows[1]['directors']),180)

    def test_zero_plan_coefficient_is_undefined(self):
        self.plan.lines.all().delete();self.line('A',0)
        row=self.run_calc().results['rows'][0]
        self.assertIsNone(row['combined_coefficient']);self.assertIsNone(row['directors_coefficient'])

    def test_bad_segment_distribution_does_not_replace_saved_results(self):
        saved=self.run_calc()
        self.plan.lines.filter(subdivision='A').update(segment_amounts={'O0':'1'})
        with self.assertRaises(ValidationError):calculate(self.plan.pk,self.version.pk)
        self.assertEqual(IntegralPlan2026Calculation.objects.count(),1)
        saved.refresh_from_db();self.assertEqual(Decimal(saved.results['total']['plan']),10000)

    def test_draft_version_cannot_be_used(self):
        with self.assertRaisesMessage(ValidationError,'утверждённую'):
            calculate(self.plan.pk,self.version.pk)

    def test_saved_snapshot_does_not_change_when_plan_changes(self):
        saved=self.run_calc();old=saved.results
        self.plan.lines.filter(subdivision='A').update(amount_usd=5000,segment_amounts={'O0':'5000','O1':'0','O2':'0','O3':'0','O4':'0'})
        saved.refresh_from_db();self.assertEqual(saved.results,old)
        new=calculate(self.plan.pk,self.version.pk)
        self.assertNotEqual(new.pk,saved.pk)
        self.assertEqual(Decimal(new.results['total']['plan']),14000)

    def test_excel_has_two_numeric_coefficient_columns_and_details(self):
        saved=self.run_calc();book=load_workbook(workbook(saved))
        self.assertEqual(book.sheetnames,['Интегральные 2026','Директора','Основание'])
        self.assertEqual(book.worksheets[0].cell(4,4).value,.03)
        self.assertEqual(book.worksheets[0].cell(4,6).value,.05)
        self.assertEqual(book.worksheets[0].cell(4,4).number_format,'0.0000%')
        self.assertEqual(book.worksheets[0].cell(6,4).value,.048)

    def test_report_post_saves_and_downloads_without_sql_agent(self):
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        url=reverse('motivation_report_integral_2026')
        response=self.client.post(url,{'scenario':self.plan.pk,'coefficient_version':self.version.pk},follow=True)
        self.assertContains(response,'Коэффициент Политики + Продажи')
        self.assertContains(response,'Коэффициент Директора')
        self.assertEqual(IntegralPlan2026Calculation.objects.count(),1)
        saved=IntegralPlan2026Calculation.objects.get()
        self.assertEqual(self.client.get(url+f'?calculation={saved.pk}&export=xlsx').status_code,200)
        self.assertContains(self.client.get(reverse('motivation_reports')),'Интегральные коэффициенты')

    def test_anonymous_is_denied(self):
        self.client.logout()
        self.assertIn(self.client.get(reverse('motivation_report_integral_2026')).status_code,(302,403))

    def test_merged_branches_and_same_director_are_not_counted_twice(self):
        self.b.merged_into=self.a;self.b.merge_policy='target';self.b.save()
        person=DirectorEmployee.objects.get(full_name='Director A');person.subdivisions.add(self.b)
        data=self.run_calc().results
        self.assertEqual(len(data['rows']),1)
        self.assertEqual(Decimal(data['rows'][0]['plan']),10000)
        self.assertEqual(Decimal(data['rows'][0]['combined']),300)
        self.assertEqual(data['rows'][0]['director_count'],3)
        self.assertEqual(Decimal(data['rows'][0]['directors']),700)

    def test_readonly_user_can_view_and_export_but_not_create_snapshots(self):
        from base.access import MOTIVATION_VIEWER
        saved=self.run_calc()
        user=User.objects.create_user('integral-viewer')
        user.groups.add(Group.objects.get(name=MOTIVATION_VIEWER))
        self.client.force_login(user)
        url=reverse('motivation_report_integral_2026')
        self.assertEqual(self.client.get(url+f'?calculation={saved.pk}').status_code,200)
        self.assertEqual(self.client.get(url+f'?calculation={saved.pk}&export=xlsx').status_code,200)
        self.assertEqual(self.client.post(url,{'scenario':self.plan.pk,'coefficient_version':self.version.pk}).status_code,403)
        self.assertEqual(IntegralPlan2026Calculation.objects.count(),1)

    def test_missing_exact_rates_are_explicit_and_missing_whole_type_is_undefined(self):
        alternate=Goods.objects.create(goods_key='Unknown',planning_group_sales='1. RAC',group='AC',brand='Other')
        self.line('A',100,good=alternate)
        row=self.run_calc().results['rows'][0]
        self.assertEqual(Decimal(row['combined']),33)
        self.assertTrue(any('минимум' in note for note in row['notes']))

    def test_financier_modeler_and_general_director_can_calculate_only_this_report(self):
        from base.access import FINANCIER,MODELER,DIRECTOR
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        url=reverse('motivation_report_integral_2026')
        for role in (FINANCIER,MODELER,DIRECTOR):
            user=User.objects.create_user('integral-'+role)
            user.groups.add(Group.objects.get(name=role));self.client.force_login(user)
            with self.subTest(role=role):
                self.assertEqual(self.client.get(url).status_code,200)
                response=self.client.post(url,{'scenario':self.plan.pk,'coefficient_version':self.version.pk})
                self.assertEqual(response.status_code,302)
        self.assertEqual(IntegralPlan2026Calculation.objects.count(),3)

    def test_missing_whole_type_is_undefined_not_zero(self):
        SubdivisionCoeff.objects.filter(type_coeff=self.types[1]).delete()
        data=self.run_calc().results
        self.assertIsNone(data['rows'][0]['combined'])
        self.assertIsNone(data['total']['combined_coefficient'])
        self.assertIsNotNone(data['rows'][0]['directors_coefficient'])

    def test_excel_branch_names_cannot_become_formulas(self):
        self.a.subdivision_key='=1+1';self.a.save()
        self.plan.lines.filter(subdivision='A').update(subdivision='=1+1')
        book=load_workbook(workbook(self.run_calc()))
        cell=book.worksheets[0].cell(4,1)
        self.assertEqual(cell.value,'=1+1');self.assertEqual(cell.data_type,'s')

    def test_branch_director_cannot_read_company_wide_integrals(self):
        from base.access import BRANCH_DIRECTOR
        user=User.objects.create_user('integral-branch-director')
        user.groups.add(Group.objects.get(name=BRANCH_DIRECTOR));self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('motivation_report_integral_2026')).status_code,403)


import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1','Optional Chromium check')
class IntegralPlan2026BrowserTests(StaticLiveServerTestCase):
    def test_saved_two_coefficients_and_excel(self):
        from playwright.sync_api import sync_playwright,expect
        from django.conf import settings
        from pathlib import Path
        from io import BytesIO
        IntegralPlan2026Tests.setUp(self)
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True)
            page=browser.new_page(viewport={'width':1600,'height':1100})
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+reverse('motivation_report_integral_2026'))
            page.locator('select[name=scenario]').select_option(str(self.plan.pk))
            page.locator('select[name=coefficient_version]').select_option(str(self.version.pk))
            page.get_by_role('button',name='Рассчитать и сохранить',exact=True).click()
            expect(page.get_by_role('columnheader',name='Коэффициент Политики + Продажи',exact=True)).to_be_visible()
            expect(page.get_by_role('columnheader',name='Коэффициент Директора',exact=True)).to_be_visible()
            a=page.locator('table').first.get_by_role('row').filter(has=page.get_by_role('cell',name='A',exact=True))
            expect(a).to_contain_text('3.0000%');expect(a).to_contain_text('5.0000%')
            page.reload()
            expect(a).to_contain_text('3.0000%')
            page.screenshot(path='/tmp/integral-plan-2026.png',full_page=True)
            with page.expect_download() as pending:page.get_by_role('link',name='Скачать Excel',exact=True).click()
            download=pending.value
            self.assertEqual(download.suggested_filename,'integral-coefficients-plan-2026.xlsx')
            book=load_workbook(BytesIO(Path(download.path()).read_bytes()))
            self.assertEqual(book.worksheets[0].cell(4,4).value,.03)
            self.assertEqual(book.worksheets[0].cell(4,6).value,.05)
            self.assertEqual(book.worksheets[0].cell(6,4).value,.048)
            browser.close()

    rates=IntegralPlan2026Tests.rates
    line=IntegralPlan2026Tests.line
    director=IntegralPlan2026Tests.director
