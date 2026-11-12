from datetime import date
from decimal import Decimal as D
from unittest.mock import Mock
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from .models import MotivationSubdivisionExclusion,Subdivision,Chanel
from .services.subdivision_scope import exclusion_scope,excluded_names,included,sql_predicate,policy_signature
from .services.sales_plans import source_sql,load_plan,calculate_plan,policy_segments
from . import test_sales_plans as fixtures
from .test_coefficient_iteration_calculation import inputs,row


class ExclusionDirectoryTests(TestCase):
    def test_seed_names_are_shared_across_years_sources_and_sql(self):
        self.assertSetEqual(set(MotivationSubdivisionExclusion.objects.values_list('subdivision_name',flat=True)),
            {'E-Com','Retail','Брендинг','Прочее','СНГ','Д-Маврикий','Буфер','Вентиляция','ДПП'})
        with exclusion_scope():
            self.assertFalse(included('  retail '));self.assertFalse(included('вЕнТиЛяЦиЯ'))
            self.assertFalse(included('ДПП'))
            self.assertTrue(included('Д-Москва'))  # Channel DPP remains a separate policy-only rule.
            for source in ('planning','erp'):
                sql=source_sql(source)
                self.assertIn("UPPER(N'Буфер')",sql);self.assertIn("UPPER(N'Прочее')",sql)
                self.assertNotIn('Chanel NOT IN',sql)
                self.assertNotIn('__PLAN_',sql)
            from .services.coefficient_iteration_2025 import build_sql
            sql=build_sql()
            self.assertIn("DELETE FROM #pf_base WHERE NOT (",sql)
            self.assertNotIn('__MOTIVATION_',sql)
            self.assertIn("Канал=N'ДПП' AND LTRIM(RTRIM([ТипКоэф]))=N'Политики'",sql)

    def test_edits_apply_next_operation_and_keep_running_scope_stable(self):
        with exclusion_scope():
            before=policy_signature()
            self.assertTrue(included('Test branch'))
            MotivationSubdivisionExclusion.objects.create(subdivision_name='Test branch')
            self.assertTrue(included('Test branch'))
        with exclusion_scope():
            self.assertFalse(included('Test branch'))
            self.assertNotEqual(policy_signature(),before)
            self.assertIn("N'Test branch'",source_sql('erp'))
        MotivationSubdivisionExclusion.objects.filter(subdivision_name='Test branch').update(enabled=False)
        with exclusion_scope():self.assertTrue(included('Test branch'))

    def test_empty_dictionary_allows_all_names_and_quotes_are_escaped(self):
        MotivationSubdivisionExclusion.objects.all().delete()
        with exclusion_scope():
            self.assertTrue(included('Retail'));self.assertEqual(sql_predicate('x'),'1=1')
        entry=MotivationSubdivisionExclusion.objects.create(subdivision_name="O'Branch")
        with exclusion_scope():self.assertIn("N'O''Branch'",source_sql('planning'))
        duplicate=MotivationSubdivisionExclusion(subdivision_name=" o'branch ")
        with self.assertRaises(ValidationError):duplicate.full_clean()

    def test_manager_filters_by_name_not_physical_key(self):
        channel=Chanel.objects.create(chanel_name='Test')
        for key,name in [('Retail','Д-Астрахань'),('OTHER-KEY','Retail')]:
            Subdivision.all_objects.create(subdivision_key=key,subdivision_name=name,subdivision_global=name,chanel=channel)
        with exclusion_scope():
            self.assertTrue(Subdivision.objects.filter(subdivision_key='Retail').exists())
            self.assertFalse(Subdivision.objects.filter(subdivision_key='OTHER-KEY').exists())

    def test_admin_directory_is_editable_and_requires_permissions(self):
        from django.contrib.auth.models import User
        user=User.objects.create_superuser('exclusion-admin','a@example.test','pw')
        self.client.force_login(user)
        response=self.client.get(reverse('admin:admin_motivation_motivationsubdivisionexclusion_changelist'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'E-Com')
        self.client.force_login(User.objects.create_user('reader'))
        self.assertEqual(self.client.get(reverse('admin:admin_motivation_motivationsubdivisionexclusion_changelist')).status_code,403)


class FullPlanPolicyBaseTests(TestCase):
    def setUp(self):
        self.fixture=fixtures.SalesPlanTests();self.fixture.setUp()
        self.plan=self.fixture.scenario
        self.fixture.cursor.description += [('SubdivisionKey',),('Chanel',)]
        a,b=self.fixture.rows()
        self.fixture.cursor.fetchall.return_value=[(*a,'A-DEALER','Дилерский'),('A',*b[1:],'A-DPP','ДПП')]

    def test_full_plan_keeps_dpp_and_policy_base_is_separate_in_both_summaries(self):
        load_plan(self.plan.pk,self.fixture.connector)
        line=self.plan.lines.get(subdivision='A')
        self.assertEqual(line.amount_usd,D(1500))
        self.assertEqual(sum(policy_segments(line.segment_amounts,'Sales')),D(1000))
        calculate_plan(self.plan.pk)
        line.refresh_from_db()
        self.assertEqual(line.policies_usd,D(200));self.assertEqual(line.sales_usd,D(45))
        global_line=self.plan.lines.get(subdivision='')
        self.assertEqual(global_line.policies_usd,D(100));self.assertEqual(global_line.sales_usd,D(75))
        from .services.plan_summary import summary_report
        summary=summary_report(self.plan,{})
        self.assertEqual(summary['summary_nodes'][0]['global'],D(175))
        # The independent private 100% scenario consumes the same saved key receipts.
        self.plan.refresh_from_db()
        from .services.coefficient_iteration_calculation import RateBook,at_full_plan,plan_rows
        payload,mappings,_,people=inputs()
        rates=RateBook(payload,mappings,{1:'A-DEALER',2:'A-DPP'},people,{1:'A',2:'A'})
        rows,_=at_full_plan(plan_rows(self.plan,2026),rates,2027,D(80),D(20))
        self.assertEqual(next(r['total'] for r in rows if r['kind']=='Политики'),D(100))
        self.assertEqual(next(r['total'] for r in rows if r['kind']=='Продажи'),D(300))

    def test_new_dictionary_name_excludes_all_its_physical_keys_on_import(self):
        MotivationSubdivisionExclusion.objects.create(subdivision_name='A')
        from django.core.exceptions import ValidationError
        with self.assertRaisesMessage(ValidationError,'пуст'):load_plan(self.plan.pk,self.fixture.connector)
        self.assertFalse(self.plan.lines.exists())

    def test_heat_is_excluded_by_planning_group_only(self):
        values={f'O{i}':str(i+1) for i in range(5)}
        self.assertEqual(policy_segments(values,' 5. heat '),[D(0)]*5)
        self.assertEqual(sum(policy_segments(values,'1. RAC')),D(15))


    def test_dpp_only_needs_sales_and_director_rates(self):
        from .services.coefficient_iteration_calculation import RateBook,at_full_plan
        payload,mappings,_,people=inputs()
        payload['branches']['blocks']=[payload['branches']['blocks'][1]]
        rates=RateBook(payload,mappings,{1:'A',2:'B'},people)
        rows,_=at_full_plan([{**row('A'),'channel':'ДПП'}],rates,2027,D(80),D(20))
        self.assertNotIn('Политики',{r['kind'] for r in rows})
        self.assertEqual(next(r['total'] for r in rows if r['kind']=='Продажи'),D(20))
