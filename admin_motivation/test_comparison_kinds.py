from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from .services.comparison import compare_plan
from .models import GlobalCoeff,TypeCoeff,CuratorCoefficient,CuratorEmployee


class ComparisonKindTests(TestCase):
    def setUp(self):
        from .test_comparison import ComparisonTests
        ComparisonTests.setUp(self)
        self.current=self.draft(date(2026,1,1),.2,True)

    def approve(self,*args,**kwargs):
        from .test_comparison import ComparisonTests
        return ComparisonTests.approve(self,*args,**kwargs)

    def draft(self,*args,**kwargs):
        from .test_comparison import ComparisonTests
        return ComparisonTests.draft(self,*args,**kwargs)

    def test_subset_changes_total_percent_and_markup_on_the_same_plan(self):
        self.plan.lines.filter(subdivision='').update(amount_ddp_usd=Decimal(900))
        all_result=compare_plan(self.plan,self.current)['variants']['current']
        for selected in (['policies'],['sales'],['policies','sales']):
            result=compare_plan(self.plan,self.current,selected_kinds=selected)['variants']['current']
            self.assertEqual(Decimal(result['total']),sum(Decimal(all_result[k]) for k in selected))
            self.assertEqual(result['plan'],all_result['plan'])
            self.assertEqual(Decimal(result['markup']),Decimal(result['plan'])/(Decimal(result['ddp'])+Decimal(result['total'])))
            self.assertEqual(Decimal(result['plan_percent']),Decimal(result['total'])/Decimal(result['plan'])*100)

    def test_global_curator_is_a_fourth_independently_selectable_amount(self):
        kind=TypeCoeff.objects.get(type_coeff_name='Кураторы')
        originals=list(GlobalCoeff.objects.filter(version=self.current,type_coeff__type_coeff_name='Политики'))
        for c in originals:
            GlobalCoeff.objects.create(version=self.current,goods=c.goods,type_coeff=kind,segment=c.segment,motivation_coeff=.03,manager_coeff=1,variation_calculate=c.variation_calculate)
        result=compare_plan(self.plan,self.current,selected_kinds=['curators'])['variants']['current']
        self.assertGreater(Decimal(result['curators']),0)
        self.assertEqual(result['total'],result['curators'])
        self.assertEqual(Decimal(result['policies']),0)
        all_result=compare_plan(self.plan,self.current)['variants']['current']
        self.assertEqual(Decimal(all_result['total']),sum(Decimal(all_result[k]) for k in ('policies','sales','directors','curators')))

    def test_empty_unknown_duplicate_or_malformed_selection_rejected(self):
        for selection in ([],['other'],['sales','sales'],[{}],'sales'):
            with self.assertRaises(ValidationError):compare_plan(self.plan,self.current,selected_kinds=selection)

    def test_api_accepts_selected_kinds_and_page_shows_all_four(self):
        response=self.client.post(reverse('motivation_coefficient_comparison'),{'version':self.current.pk,'plan':self.plan.pk,'selected_kinds':['sales']},content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['selected_kinds'],['sales'])
        result=response.json()['variants']['current'];self.assertEqual(result['total'],result['sales'])
        page=self.client.get(reverse('global_coeff_admin_motivation'),{'version':self.current.pk})
        self.assertContains(page,'id="comparison-kinds-all"')
        self.assertContains(page,'data-comparison-kind value="curators"')

    def test_local_curators_use_assigned_branch_rates(self):
        employee=CuratorEmployee.objects.create(full_name='Сравнение куратор')
        employee.assigned_subdivisions().add(self.subs[0])
        for good in self.current.coefficients.values_list('goods_id',flat=True).distinct():
            CuratorCoefficient.objects.create(version=self.current,employee=employee,subdivision=self.subs[0],goods_id=good,**dict.fromkeys(('k0','k1','k2','k3','k4'),Decimal('.05')))
        result=compare_plan(self.plan,self.current,subdivision=self.subs[0],selected_kinds=['curators'])['variants']['current']
        self.assertGreater(Decimal(result['curators']),0)
        self.assertEqual(result['total'],result['curators'])
        employee.assigned_subdivisions().remove(self.subs[0])
        result=compare_plan(self.plan,self.current,subdivision=self.subs[0],selected_kinds=['curators'])['variants']['current']
        self.assertEqual(Decimal(result['total']),0)
