from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_test_case
from .models import (GlobalCoeff, SubdivisionCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff,
                     DirectorEmployee, CoefficientBalanceLog, GlobalCoeffVersion)
from .services.balancing import Budget


class BalancingTests(TestCase):
    def setUp(self):
        from .test_sales_plans import SalesPlanTests
        fixture = SalesPlanTests(); fixture.setUp(); fixture.load()
        self.fixture = fixture
        self.version, self.plan, self.sub = fixture.version, fixture.scenario, fixture.subs[0]
        authorize_test_case(self)
        self.url = reverse('motivation_coefficient_balancing')
        self.params = {'version':self.version.pk,'plan':self.plan.pk,'scope':'global','mode':'type'}

    def cell(self, kind='Политики', segment='K0', local=None):
        qs = SubdivisionCoeff.objects.filter(subdivision=local) if local else GlobalCoeff.objects.all()
        return qs.get(version=self.version,type_coeff__type_coeff_name=kind,segment__segment_name=segment)

    def key(self, **kwargs):
        return f'c:{self.cell(**kwargs).pk}'

    def post(self, action, **kwargs):
        return self.client.post(self.url, data=dict(self.params,action=action,**kwargs),content_type='application/json')

    def preview(self, **kwargs):
        fingerprint = self.post('load').json()['fingerprint']
        return self.post('preview',fingerprint=fingerprint,manual={self.key():'20%'},compensate=[self.key(segment='K1')],**kwargs)

    def test_proportional_to_plan_keeps_manual_and_unselected_values(self):
        budget=Budget(self.version,self.plan)
        manual, first, second = self.key(),self.key(segment='K1'),self.key(segment='K2')
        result=budget.balance({manual:'20%'},[first,second],'type')
        self.assertEqual(Decimal(result['changes'][manual]['after']),Decimal('.2'))
        self.assertAlmostEqual(Decimal(result['changes'][first]['after']) / Decimal('.1'),Decimal(result['changes'][second]['after']) / Decimal('.1'))
        # Plan weights are 300 and 450 USD: contributions absorb the delta in a 2:3 ratio.
        delta1=(Decimal(result['changes'][first]['after'])-Decimal('.1'))*300
        delta2=(Decimal(result['changes'][second]['after'])-Decimal('.1'))*450
        self.assertAlmostEqual(delta1/delta2,Decimal(2)/3)
        self.assertLess(abs(Decimal(result['delta'])),Decimal('.005'))
        self.assertEqual(set(result['changes']),{manual,first,second})
        self.assertEqual(self.cell().motivation_coeff,.1)  # preview has no writes

    def test_type_does_not_steal_sales_budget(self):
        budget=Budget(self.version,self.plan)
        with self.assertRaisesMessage(ValidationError,'нет выбранных'):
            budget.balance({self.key():'20%'},[self.key(kind='Продажи',segment='K1')],'type')

    def test_company_can_transfer_between_types(self):
        result=Budget(self.version,self.plan).balance({self.key():'20%'},[self.key(kind='Продажи',segment='K1')],'company')
        self.assertLess(abs(Decimal(result['delta'])),Decimal('.005'))
        self.assertNotEqual(result['totals'][0]['delta'],'0.0')

    def test_local_company_transfers_between_subdivisions_only_selected_cells(self):
        a,b=self.fixture.subs
        manual=self.key(local=a)
        compensate=self.key(local=b,kind='Продажи',segment='K1')
        budget=Budget(self.version,self.plan,True)
        result=budget.balance({manual:'21%'},[compensate],'company')
        self.assertEqual(set(result['changes']),{manual,compensate})
        self.assertLess(abs(Decimal(result['delta'])),Decimal('.005'))
        with self.assertRaisesMessage(ValidationError,'нет выбранных'):
            budget.balance({manual:'21%'},[compensate],'subdivision')

    def test_local_subdivision_keeps_other_branch_untouched(self):
        budget=Budget(self.version,self.plan,True,self.sub)
        result=budget.balance({self.key(local=self.sub):'21%'},[self.key(local=self.sub,kind='Продажи',segment='K1')],'subdivision')
        self.assertEqual({row['subdivision'] for row in result['totals']},{'A'})
        with self.assertRaisesMessage(ValidationError,'другой версии'):
            budget.balance({self.key(local=self.fixture.subs[1]):'30%'},[self.key(local=self.sub)],'company')

    def test_directors_global_are_multiplied_per_branch_employee(self):
        for n in range(2):
            staff=DirectorEmployee.objects.create(full_name=f'Director {n}')
            SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.sub,employee=staff,goods=self.fixture.good,**{f'k{i}':'.01' for i in range(5)})
        director=GlobalDirectorCoeff.objects.create(version=self.version,goods=self.fixture.good,**{f'k{i}':'.01' for i in range(5)})
        budget=Budget(self.version,self.plan)
        data=budget.payload()
        premium=sum((Decimal(cell['premium']) for cell in data['cells'] if cell['kind']=='Директор'),Decimal(0))
        self.assertEqual(premium,Decimal('20'))
        result=budget.balance({f'd:{director.pk}:0':'2%'},[f'd:{director.pk}:1'],'type')
        self.assertLess(abs(Decimal(result['delta'])),Decimal('.005'))
        self.assertEqual(Decimal(result['changes'][f'd:{director.pk}:1']['after']),Decimal('.005'))

    def test_fallback_minimum_is_recomputed_after_edit(self):
        self.plan.lines.update(group='Other',brand='Other')
        budget=Budget(self.version,self.plan)
        self.assertGreater(budget.fallbacks,0)
        # Changing just one of the tied minimum providers cannot raise the group's budget.
        result=budget.balance({self.key():'20%'},[self.key(segment='K1')],'type')
        self.assertEqual(Decimal(result['delta']),0)
        self.assertEqual(set(result['changes']),{self.key()})

    def test_invalid_selections_limits_and_zero_pool(self):
        budget=Budget(self.version,self.plan)
        for manual,pool in [({self.key():'NaN'},[self.key(segment='K1')]),({self.key():'101%'},[self.key(segment='K1')]),({self.key():'20%'},[self.key()]),({self.key():'20%'},['c:99999']),({self.key():'20%'},[])]:
            with self.subTest(manual=manual,pool=pool),self.assertRaises(ValidationError):budget.balance(manual,pool,'type')
        self.cell(segment='K1').__class__.objects.filter(pk=self.cell(segment='K1').pk).update(motivation_coeff=0)
        with self.assertRaisesMessage(ValidationError,'ненулевых'):
            Budget(self.version,self.plan).balance({self.key():'20%'},[self.key(segment='K1')],'type')

    def test_precision_failure_does_not_claim_budget_preserved(self):
        staff=DirectorEmployee.objects.create(full_name='D')
        row=SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.sub,employee=staff,goods=self.fixture.good,**{f'k{i}':'.01' for i in range(5)})
        self.plan.lines.filter(subdivision='A').update(segment_amounts={'O0':'1','O1':'1000000','O2':'0','O3':'0','O4':'0'})
        with self.assertRaisesMessage(ValidationError,'точность хранения'):
            Budget(self.version,self.plan,True,self.sub).balance({f'd:{row.pk}:0':'2%'},[f'd:{row.pk}:1'],'type')

    def test_page_load_preview_save_audit_and_replay_guard(self):
        self.assertContains(self.client.get(self.url),'Балансировка премии')
        self.assertEqual(self.post('load').status_code,200)
        response=self.preview();self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(CoefficientBalanceLog.objects.exists())
        token=response.json()['token']
        response=self.post('save',token=token);self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.cell().motivation_coeff,.2)
        self.assertEqual(self.cell(segment='K1').motivation_coeff,.05)
        self.assertEqual(CoefficientBalanceLog.objects.count(),1)
        self.assertEqual(self.post('save',token=token).status_code,400)
        self.assertEqual(CoefficientBalanceLog.objects.count(),1)

    def test_stale_plan_or_coefficient_rejects_save_atomically(self):
        for source in ['plan','coefficient']:
            response=self.preview();self.assertEqual(response.status_code,200,response.content)
            if source=='plan':
                self.plan.lines.filter(subdivision='A').update(segment_amounts={'O0':'101','O1':'200','O2':'300','O3':'100','O4':'300'})
            else:GlobalCoeff.objects.filter(pk=self.cell(segment='K4').pk).update(motivation_coeff=.11)
            response=self.post('save',token=response.json()['token'])
            self.assertEqual(response.status_code,400,response.content)
            self.assertEqual(self.cell().motivation_coeff,.1)
            self.assertFalse(CoefficientBalanceLog.objects.exists())

    def test_approved_version_analysis_allowed_save_denied(self):
        from django.db import models
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=self.version.pk),status='approved')
        response=self.preview();self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(response.json()['editable'])
        self.assertEqual(self.post('save',token=response.json()['token']).status_code,400)
        self.assertFalse(CoefficientBalanceLog.objects.exists())

    def test_admin_only_and_tampered_review(self):
        from django.contrib.auth.models import User,Group
        for role in ['Смотрящий','Финансист','Генеральный директор']:
            user=User.objects.create_user(username=role);user.groups.add(Group.objects.get_or_create(name=role)[0]);self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code,403)
            self.assertEqual(self.post('load').status_code,403)
        authorize_test_case(self)
        self.assertEqual(self.post('save',token='tampered').status_code,400)

    def test_version_date_limits_budget_inputs_and_empty_plan(self):
        self.version.effective_from=date(2027,1,1)
        with self.assertRaisesMessage(ValidationError,'нет строк'):
            Budget(self.version,self.plan)

    def test_last_approved_is_copied_atomically_before_saving(self):
        from django.db import models
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=self.version.pk),status='approved')
        reviewed=self.preview();self.assertEqual(reviewed.status_code,200,reviewed.content)
        self.assertTrue(reviewed.json()['can_copy'])
        saved=self.post('save',token=reviewed.json()['token'],create_draft=True)
        self.assertEqual(saved.status_code,200,saved.content)
        new=GlobalCoeffVersion.objects.get(pk=saved.json()['version'])
        self.assertNotEqual(new.pk,self.version.pk)
        self.assertEqual(new.status,'draft')
        self.version.refresh_from_db();self.assertEqual(self.version.status,'approved')
        self.assertEqual(self.cell().motivation_coeff,.1)
        self.assertEqual(new.coefficients.get(type_coeff__type_coeff_name='Политики',segment__segment_name='K0').motivation_coeff,.2)
        self.assertEqual(CoefficientBalanceLog.objects.get().version,new)

    def test_local_director_cells_survive_storage_and_audit(self):
        staff=DirectorEmployee.objects.create(full_name='Local director')
        director=SubdivisionDirectorCoeff.objects.create(version=self.version,goods=self.fixture.good,subdivision=self.sub,employee=staff,**{f'k{i}':'.01' for i in range(5)})
        self.params.update(scope='local',subdivision=self.sub.pk)
        fingerprint=self.post('load').json()['fingerprint']
        reviewed=self.post('preview',fingerprint=fingerprint,manual={f'd:{director.pk}:0':'2%'},compensate=[f'd:{director.pk}:1'])
        self.assertEqual(reviewed.status_code,200,reviewed.content)
        saved=self.post('save',token=reviewed.json()['token']);self.assertEqual(saved.status_code,200,saved.content)
        director.refresh_from_db();self.assertEqual(director.k0,Decimal('.02'));self.assertEqual(director.k1,Decimal('.005'))

    def test_table_fallback_keeps_approved_historical_minima(self):
        from django.db import models
        from .services.versions import create_version
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=self.version.pk),status='approved')
        current=create_version(date(2026,1,1),source_version=self.version.pk)
        current.coefficients.update(motivation_coeff=.2)
        self.plan.lines.update(planning_group_sales='Missing group')
        budget=Budget(current,self.plan)
        self.assertEqual(sum(Decimal(row['before']) for row in budget.payload()['totals']),Decimal('225'))
        self.assertTrue(all(GlobalCoeff.objects.get(pk=int(cell['id'].split(':')[1])).version_id==current.pk for cell in budget.payload()['cells']))
        rows=list(current.coefficients.filter(type_coeff__type_coeff_name='Политики').order_by('pk'))
        result=budget.balance({f'c:{rows[0].pk}':'30%'},[f'c:{rows[1].pk}'],'type')
        self.assertEqual(Decimal(result['delta']),0)

    def test_failed_post_write_verification_rolls_back_every_cell(self):
        from unittest.mock import patch
        from .models import GlobalCoeffQuerySet
        reviewed=self.preview();self.assertEqual(reviewed.status_code,200,reviewed.content)
        original=GlobalCoeffQuerySet.bulk_update
        manual=self.cell()
        def corrupt(queryset, objects, fields, **kwargs):
            count=original(queryset,objects,fields,**kwargs)
            queryset.filter(pk=manual.pk).update(motivation_coeff=.99)
            return count
        with patch.object(GlobalCoeffQuerySet,'bulk_update',autospec=True,side_effect=corrupt):
            saved=self.post('save',token=reviewed.json()['token'])
        self.assertEqual(saved.status_code,400,saved.content)
        self.assertEqual(self.cell().motivation_coeff,.1)
        self.assertEqual(self.cell(segment='K1').motivation_coeff,.1)
        self.assertFalse(CoefficientBalanceLog.objects.exists())

    def test_project_administrator_has_access_without_global_administration(self):
        from django.contrib.auth.models import User
        from base.models import UserProjectAccess
        from base.access import ADMIN
        user=User.objects.create_user(username='balance-project-admin')
        UserProjectAccess.objects.create(user=user,project='motivation',role=ADMIN)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.post('load').status_code,200)

    def test_large_budget_uses_direct_weighted_solution_for_sparse_edits(self):
        from unittest.mock import patch
        from .services.balancing import stored
        budget=Budget(self.version,self.plan)
        manual,auto,untouched=self.key(),self.key(segment='K1'),self.key(segment='K2')
        budget.terms=[term for branch in range(3000) for term in [
            (str(branch),'Политики',(manual,),Decimal(100)),
            (str(branch),'Политики',(auto,),Decimal(200)),
            (str(branch),'Политики',(untouched,),Decimal(1000))]]
        with patch('admin_motivation.services.balancing.stored',wraps=stored) as rounding:
            result=budget.balance({manual:'20%'},[auto],'type')
        self.assertEqual(Decimal(result['changes'][auto]['after']),Decimal('.05'))
        self.assertEqual(Decimal(result['delta']),0)
        self.assertLessEqual(rounding.call_count,3)

    def test_sparse_search_keeps_fixed_minimum_fallback_dynamic(self):
        budget=Budget(self.version,self.plan)
        manual,auto,fixed=self.key(),self.key(segment='K1'),self.key(segment='K2')
        budget.cells[fixed]['value']='.15'
        budget.terms=[('A','Политики',(manual,),Decimal(100)),
                      ('A','Политики',(auto,fixed),Decimal(200))]
        result=budget.balance({manual:'20%'},[auto],'type')
        self.assertEqual(Decimal(result['changes'][auto]['after']),Decimal('.05'))
        self.assertEqual(Decimal(result['delta']),0)
        self.assertEqual(set(result['changes']),{manual,auto})

    def test_monthly_preaggregation_preserves_budget_and_source_fingerprint(self):
        first=Budget(self.version,self.plan)
        for line in list(self.plan.lines.exclude(subdivision='')):
            line.pk=None;line.plan_date=date(2026,11,1);line.save()
        second=Budget(self.version,self.plan)
        self.assertNotEqual(first.fingerprint,second.fingerprint)
        self.assertEqual(len(first.terms),len(second.terms))
        result=second.balance({self.key():'20%'},[self.key(segment='K1')],'type')
        self.assertEqual(Decimal(result['before']),Decimal(450))
        self.assertEqual(Decimal(result['after']),Decimal(450))
        self.assertEqual(Decimal(result['changes'][self.key(segment='K1')]['after']),Decimal('.05'))
        signature=second.fingerprint
        self.plan.lines.filter(plan_date=date(2026,11,1)).update(plan_date=date(2026,12,1))
        self.assertNotEqual(signature,Budget(self.version,self.plan).fingerprint)

    def test_global_display_summary_is_company_total_and_local_keeps_branches(self):
        response=self.post('load');self.assertEqual(response.status_code,200,response.content)
        data=response.json()
        self.assertEqual({row['subdivision'] for row in data['display_totals']},{'Компания'})
        self.assertEqual({row['subdivision'] for row in data['totals']},{'A','B'})
        self.assertEqual(sum(Decimal(row['before']) for row in data['display_totals']),sum(Decimal(row['before']) for row in data['totals']))
        reviewed=self.preview();self.assertEqual(reviewed.status_code,200,reviewed.content)
        data=reviewed.json()
        self.assertEqual({row['subdivision'] for row in data['display_totals']},{'Компания'})
        self.assertEqual(sum(Decimal(row['after']) for row in data['display_totals']),Decimal(data['after']))
        self.params.update(scope='local',mode='company')
        local=self.post('load').json()
        self.assertEqual({row['subdivision'] for row in local['display_totals']},{'A','B'})
