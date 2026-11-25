from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from base.testing import authorize_admin
from .models import (Chanel,Subdivision,Goods,GlobalCoeffVersion,TypeCoeff,SegmentCoeff,VariationCalculate,
    GlobalCoeff,SubdivisionCoeff,SalesPlanScenario,SalesPlanLine,CoefficientGroupCopyLog,
    GlobalDirectorCoeff,SubdivisionDirectorCoeff,DirectorEmployee,CuratorEmployee,CuratorCoefficient)
from .services import global_from_branches as service


class GlobalFromBranchesTests(TestCase):
    def setUp(self):
        self.user=authorize_admin(self.client)
        self.version=GlobalCoeffVersion.objects.create(effective_from=date(2026,1,1))
        self.formula,_=VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')
        self.kind,_=TypeCoeff.objects.get_or_create(type_coeff_name='Политики')
        self.segments=[SegmentCoeff.objects.get_or_create(segment_name=f'K{i}')[0] for i in range(5)]
        ch=Chanel.objects.create(chanel_name='Weighted')
        self.branches=[Subdivision.objects.create(subdivision_key=n,subdivision_name=n,subdivision_global=n,chanel=ch) for n in ('WA','WB')]
        self.good=Goods.objects.create(goods_key='WEIGHTED',planning_group_sales='RAC',group='Split',brand='X')
        self.plan=SalesPlanScenario.objects.create(title='Weighted plan',source_version='Test')
        for branch,value,amount in zip(self.branches,(.01,.03),(100,300)):
            for segment in self.segments:
                SubdivisionCoeff.objects.create(version=self.version,subdivision=branch,goods=self.good,type_coeff=self.kind,segment=segment,motivation_coeff=value,manager_coeff=1,variation_calculate=self.formula)
            SalesPlanLine.objects.create(scenario=self.plan,plan_date=date(2026,1,1),subdivision=branch.subdivision_key,planning_group_sales='RAC',group='Split',brand='X',amount_usd=amount*5,segment_amounts={f'O{i}':str(amount) for i in range(5)})
        for segment in self.segments:
            GlobalCoeff.objects.create(version=self.version,goods=self.good,type_coeff=self.kind,segment=segment,motivation_coeff=.04,manager_coeff=.9,variation_calculate=self.formula)
        self.version.refresh_from_db()

    def preview(self,groups=None):
        self.version.refresh_from_db()
        return service.preview(self.version,self.plan,groups or ['policies'])

    def apply(self,result,groups=None):
        return service.apply(self.user,self.version.pk,self.plan.pk,groups or ['policies'],self.version.revision,result['digest'])

    def test_weighted_rates_preserve_plan_budget_and_formula_and_audit(self):
        result=self.preview();self.assertTrue(result['ready'])
        self.assertEqual({r['after'] for r in result['rows']},{'0.025000'})
        self.assertEqual({r['weight'] for r in result['rows']},{'400'})
        revision=self.version.revision
        self.assertEqual(self.apply(result),5)
        for row in GlobalCoeff.objects.filter(version=self.version):
            self.assertEqual(row.motivation_coeff,.025);self.assertEqual(row.manager_coeff,.9);self.assertEqual(row.variation_calculate,self.formula)
        self.assertEqual(Decimal('0.025')*400,Decimal('.01')*100+Decimal('.03')*300)
        self.assertEqual(SubdivisionCoeff.objects.filter(motivation_coeff=.01).count(),5)
        log=CoefficientGroupCopyLog.objects.get(source_group='subdivisions')
        self.assertEqual(log.changes[0]['plan'],self.plan.pk)
        self.version.refresh_from_db();self.assertEqual(self.version.revision,revision+1)

    def test_zero_weight_keeps_existing_rate(self):
        self.plan.lines.update(segment_amounts={'O0':'100'})
        result=self.preview();self.assertTrue(result['ready'])
        self.assertTrue(all(r['after'] is None for r in result['rows'] if r['segment']))
        self.apply(result)
        self.assertEqual(GlobalCoeff.objects.get(version=self.version,segment=self.segments[4]).motivation_coeff,.04)

    def test_missing_source_skips_only_its_weight_and_applies_others(self):
        SubdivisionCoeff.objects.filter(subdivision=self.branches[1],segment=self.segments[3]).delete()
        result=self.preview();self.assertTrue(result['ready']);self.assertTrue(result['missing'])
        row=next(r for r in result['rows'] if r['segment']==3)
        self.assertEqual(row['after'],'0.010000');self.assertEqual(row['weight'],'100')
        self.apply(result)
        self.assertEqual(GlobalCoeff.objects.get(version=self.version,segment=self.segments[3]).motivation_coeff,.01)

    def test_changed_plan_stale_revision_and_negative_weight_are_rejected(self):
        result=self.preview()
        self.plan.lines.filter(subdivision='WA').update(segment_amounts={'O0':'500'})
        with self.assertRaises(ValidationError):self.apply(result)
        result=self.preview();cell=GlobalCoeff.objects.first();cell.motivation_coeff=.05;cell.save()
        with self.assertRaises(ValidationError):self.apply(result)
        self.plan.lines.update(segment_amounts={'O0':'-1'})
        result=self.preview();self.assertFalse(result['ready']);self.assertTrue(result['negative'])

    def test_employee_rates_are_summed_per_branch_and_curators_stay_physical(self):
        curator_kind,_=TypeCoeff.objects.get_or_create(type_coeff_name='Кураторы')
        GlobalDirectorCoeff.objects.create(version=self.version,goods=self.good,variation_calculate=self.formula,**{f'k{i}':'.04' for i in range(5)})
        for segment in self.segments:
            GlobalCoeff.objects.create(version=self.version,goods=self.good,type_coeff=curator_kind,segment=segment,motivation_coeff=.04,manager_coeff=1,variation_calculate=self.formula)
        for model,employee_model in ((SubdivisionDirectorCoeff,DirectorEmployee),(CuratorCoefficient,CuratorEmployee)):
            for n,(branch,value) in enumerate(zip(self.branches,('.01','.03'))):
                person=employee_model.objects.create(full_name=f'{model.__name__}{n}')
                person.subdivisions.add(branch)
                model.objects.create(version=self.version,goods=self.good,employee=person,subdivision=branch,**{f'k{i}':value for i in range(5)})
        result=self.preview(['directors','curators'])
        self.assertTrue(result['ready']);self.assertEqual({r['after'] for r in result['rows']},{'0.025000'})
        self.apply(result,['directors','curators'])
        self.assertEqual(GlobalDirectorCoeff.objects.get(version=self.version).k0,Decimal('.025'))

    def test_preview_http_confirmation_required_and_actor_permission(self):
        url=reverse('motivation_global_from_subdivisions')
        data={'version':self.version.pk,'plan':self.plan.pk,'groups':['policies'],'action':'preview'}
        response=self.client.post(url,data)
        self.assertContains(response,'2.500000')
        token=response.context['preview_token']
        response=self.client.post(url,{**data,'action':'apply','preview_token':token})
        self.assertContains(response,'Подтвердите замену')
        self.assertEqual(self.client.post(url,{**data,'action':'apply','preview_token':token,'confirm':'yes'}).status_code,302)
        self.client.force_login(User.objects.create_user('weighted-no-access'))
        self.assertEqual(self.client.get(url,{'version':self.version.pk}).status_code,403)

    def test_unknown_plan_product_and_missing_global_cell_are_skipped(self):
        GlobalCoeff.objects.filter(segment=self.segments[0]).delete()
        result=self.preview();self.assertTrue(result['ready']);self.assertTrue(result['missing_targets'])
        self.apply(result)
        self.assertFalse(GlobalCoeff.objects.filter(version=self.version,segment=self.segments[0]).exists())
        self.plan.lines.filter(subdivision='WB').update(brand='Not in directory')
        result=self.preview();self.assertTrue(result['ready']);self.assertTrue(result['unknown'])
        self.assertEqual({row['weight'] for row in result['rows'] if row['after'] is not None},{'100'})
        self.apply(result)
        self.assertEqual(GlobalCoeff.objects.get(version=self.version,segment=self.segments[1]).motivation_coeff,.01)

    def test_excluded_group_has_no_weight_and_does_not_zero_global(self):
        from .models import MotivationPlanningGroupExclusion
        MotivationPlanningGroupExclusion.objects.get_or_create(coefficient_type='Политики',value='RAC',defaults={'enabled':True})
        result=self.preview();self.assertFalse(result['ready'])
        self.assertTrue(all(row['after'] is None for row in result['rows']))
        self.assertEqual(set(GlobalCoeff.objects.values_list('motivation_coeff',flat=True)),{.04})

    def test_read_only_version_and_no_confirmation_never_modify(self):
        from django.db import models
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=self.version.pk),status='approved')
        result=self.preview()
        with self.assertRaises(ValidationError):self.apply(result)
        url=reverse('motivation_global_from_subdivisions')
        self.assertContains(self.client.get(url,{'version':self.version.pk}),'последнем черновике')

    def test_global_plan_rows_are_not_double_counted(self):
        SalesPlanLine.objects.create(scenario=self.plan,plan_date=date(2026,1,1),subdivision='',planning_group_sales='RAC',group='Split',brand='X',amount_usd=2000,segment_amounts={f'O{i}':'400' for i in range(5)})
        result=self.preview();self.assertEqual({row['weight'] for row in result['rows']},{'400'})

    def test_no_source_keeps_existing_global_and_zero_rate_is_included(self):
        SubdivisionCoeff.objects.filter(segment=self.segments[2]).delete()
        SubdivisionCoeff.objects.filter(subdivision=self.branches[0],segment=self.segments[1]).update(motivation_coeff=0)
        result=self.preview();self.assertTrue(result['ready'])
        self.assertIsNone(next(r for r in result['rows'] if r['segment']==2)['after'])
        self.assertEqual(next(r for r in result['rows'] if r['segment']==1)['after'],'0.022500')
        self.apply(result)
        self.assertEqual(GlobalCoeff.objects.get(version=self.version,segment=self.segments[2]).motivation_coeff,.04)

    def test_partial_director_matrix_uses_available_recipient_only(self):
        other_good=Goods.objects.create(goods_key='OTHER',planning_group_sales='Other',group='Other',brand='Other')
        GlobalDirectorCoeff.objects.create(version=self.version,goods=self.good,**{f'k{i}':'.04' for i in range(5)})
        for index,good in enumerate((self.good,other_good)):
            person=DirectorEmployee.objects.create(full_name=f'Partial {index}')
            person.subdivisions.add(self.branches[0])
            SubdivisionDirectorCoeff.objects.create(version=self.version,goods=good,employee=person,subdivision=self.branches[0],**{f'k{i}':'.01' for i in range(5)})
        result=self.preview(['directors']);self.assertTrue(result['ready']);self.assertTrue(result['missing'])
        self.assertEqual({r['after'] for r in result['rows']},{'0.010000'})
        self.assertEqual({r['weight'] for r in result['rows']},{'100'})
        self.apply(result,['directors'])
