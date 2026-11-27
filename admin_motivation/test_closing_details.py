from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from .test_distribution_stages import ClosingWorkflowTests
from .services.closing_coefficient import confirm, ReportMultipliers, without_correction
from .services import closing_details
from .models import PremiumDistribution, PeriodMotivationCoefficient


class ImpactTests(SimpleTestCase):
    def test_impact_is_weighted_by_payable_amount_with_personal_replacement(self):
        rows=[dict(kind='Политики',author='Иванов',payment='quarter',before='900'),dict(kind='Политики',author='Петров',payment='quarter',before='100'),dict(kind='Продажи',author='Sales',payment='quarter',before='1000')]
        result=closing_details.impact(rows,1,{'Политики':'1.1','Продажи':'0.8'},{'Политики':{'Иванов':'1.3'}})
        self.assertEqual(result['before'],2000)
        self.assertEqual(result['after'],2080)
        self.assertEqual(result['coefficient'],Decimal('1.04'))
        self.assertEqual(result['rows'][0]['factor'],Decimal('1.3'))
        self.assertEqual(result['percent'],4)

    def test_zero_signed_amounts_and_annual_catchup_stay_separate(self):
        rows=[dict(kind='Директора',author='D',payment='annual',before='0'),dict(kind='Политики',author='P',payment='catchup',before='-10')]
        result=closing_details.impact(rows,2)
        self.assertEqual(result['after'],-20)
        self.assertEqual(result['payments'][0]['coefficient'],None)
        self.assertEqual(result['payments'][1]['after'],-20)
        self.assertEqual(closing_details.impact([],2)['coefficient'],None)

    def test_round_after_factor_and_validate_numbers_and_duplicate_names(self):
        result=closing_details.impact([dict(kind='Политики',author='A',payment='quarter',before='1.0049')],2)
        self.assertEqual(result['before'],1)
        self.assertEqual(result['after'],Decimal('2.01'))
        for value in ('NaN','Infinity','-1','1.00001'):
            with self.assertRaises(ValidationError):closing_details.factor(value)
        for kinds,people in [({'Wrong':1},{}),({}, {'Продажи':{'A':1}}),({}, {'Политики':{'A':1,' a ':2}})]:
            with self.assertRaises(ValidationError):closing_details.normalize_settings(kinds,people)


class DetailedCoefficientTests(TestCase):
    setUp=ClosingWorkflowTests.setUp

    def confirm(self, period=4):
        return confirm(self.ceo,self.branch,2025,period,1,kind_coefficients={'Политики':'1.1','Продажи':'.8','Директора':'1.2'},personal_coefficients={'Политики':{'Policy':'1.3'},'Директора':{'Director':'1.5'}})

    def row(self,kind='Политики',author='Policy',period='4 Квартал'):
        return dict(Подразделение='Closing',Год=2025,КварталМотивации=period,ТипКоэф=kind,АвторСделки=author,СуммаМотивацииUSD=100,СуммаМотивацииUSDУчетПроцента=50,Остаток=10,СуммаПланUSD=1000,O0=500)

    def test_report_precedence_kind_hint_idempotence_and_inputs(self):
        self.confirm()
        for kind,author,expected in [('Политики','Policy',130),('Политики','Other',110),('Директора','Director',150),('Директора','Other',120),('Продажи','Sales',80),('Кураторы','Curator',100)]:
            row=self.row(kind,author)
            reader=ReportMultipliers(mode='period-unified-v1')
            result=reader.apply([row])
            self.assertEqual(result[0]['СуммаМотивацииUSD'],expected)
            self.assertEqual(reader.apply(result),result)
            self.assertEqual(result[0]['СуммаПланUSD'],1000)
            self.assertEqual(result[0]['O0'],500)
        row=self.row();row.pop('ТипКоэф')
        self.assertEqual(ReportMultipliers(mode='period-unified-v1',kind='Политики').apply([row])[0]['СуммаМотивацииUSD'],130)

    def test_year_annual_accrual_and_quarter_coefficients_are_independent(self):
        self.confirm()
        confirm(self.ceo,self.branch,2025,5,2,kind_coefficients={'Политики':'3'},personal_coefficients={'Политики':{'Policy':'4'}})
        row=self.row();row['AnnualUSD']=25
        result=ReportMultipliers(mode='period-unified-v1').apply([row])[0]
        self.assertEqual(result['СуммаМотивацииUSD'],130)
        self.assertEqual(result['AnnualUSD'],100)
        self.assertEqual(ReportMultipliers(mode='period-unified-v1').apply([self.row(period='Год')])[0]['СуммаМотивацииUSD'],400)

    def test_bulk_confirmation_keeps_overrides_and_audits_them(self):
        row=self.confirm()
        row=confirm(self.ceo,self.branch,2025,4,'1.2',revision=row.revision)
        self.assertEqual(row.kind_coefficients['Политики'],'1.1')
        self.assertEqual(row.history[-1]['personal_coefficients']['Политики']['Policy'],'1.3')
        with self.assertRaises(ValidationError):confirm(self.ceo,self.branch,2025,4,1,revision=1)
        with self.assertRaises(PermissionDenied):confirm(self.finance,self.branch,2025,4,1,revision=row.revision)

    def test_baseline_bypass_does_not_divide_by_zero_and_resets(self):
        confirm(self.ceo,self.branch,2025,4,0)
        token=without_correction.set(True)
        try:self.assertEqual(ReportMultipliers(mode='period-unified-v1').apply([self.row()])[0]['СуммаМотивацииUSD'],100)
        finally:without_correction.reset(token)
        self.assertEqual(ReportMultipliers(mode='period-unified-v1').apply([self.row()])[0]['СуммаМотивацииUSD'],0)

    def post(self):
        return {'year':2025,'period':4,'currency':'USD','coefficient':'1','revision':0,'sales':'.8','policies':'1.1','directors':'1.2',
                'people-TOTAL_FORMS':'1','people-INITIAL_FORMS':'0','people-0-kind':'Политики','people-0-author':'Policy','people-0-coefficient':'1.3'}

    def baseline(self):
        return [dict(branch='Closing',kind='Политики',author='Policy',payment='quarter',before='100')],self.run

    def test_modal_preview_save_access_and_stale_form(self):
        url=reverse('motivation_closing_details',args=[self.branch.pk])
        with patch('admin_motivation.closing_details_views.baseline',return_value=self.baseline()):
            response=self.client.get(url,{'year':2025,'period':4})
            self.assertContains(response,'Итоговый коэффициент')
            preview=self.client.post(url,dict(self.post(),action='preview'))
            self.assertEqual(preview.json()['comparison']['after'],'130.00')
            self.assertFalse(PeriodMotivationCoefficient.objects.exists())
            response=self.client.post(url,dict(self.post(),action='save'))
            self.assertContains(response,'Коэффициенты сохранены')
            self.assertEqual(PeriodMotivationCoefficient.objects.get().personal_coefficients['Политики']['Policy'],'1.3')
            stale=self.client.post(url,dict(self.post(),action='save'))
            self.assertContains(stale,'Множитель изменился')
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(url,{'year':2025,'period':4}).status_code,403)
        self.assertEqual(self.client.get(reverse('motivation_closing_comparisons'),{'year':2025,'period':4}).status_code,403)

    def test_locked_funds_cannot_be_modified_and_snapshot_carries_settings(self):
        row=self.confirm()
        from .services import premium_distribution as distribution
        with patch.object(distribution,'read_source',return_value=(deepcopy(self.funds),deepcopy(self.rows))):
            batch=distribution.create(self.admin,self.run,self.branch,4,'USD')
        self.assertEqual(batch.source['workflow']['kind_coefficients']['Продажи'],'0.8')
        with self.assertRaises(ValidationError):confirm(self.ceo,self.branch,2025,4,2,revision=row.revision,kind_coefficients={})

    def test_comparison_endpoint_and_source_failure_do_not_fabricate_zero(self):
        self.confirm()
        url=reverse('motivation_closing_comparisons')
        with patch('admin_motivation.services.closing_plan_fact.load',return_value={}), patch('admin_motivation.closing_details_views.baseline',return_value=self.baseline()):
            response=self.client.get(url,{'year':2025,'period':4})
            self.assertEqual(response.json()['rows'][str(self.branch.pk)]['after'],'130.00')
        with patch('admin_motivation.closing_details_views.baseline',side_effect=ValidationError('Нет расчёта')):
            self.assertEqual(self.client.get(url,{'year':2025,'period':4}).status_code,400)

    def test_personal_override_cannot_target_a_different_branch_or_kind(self):
        url=reverse('motivation_closing_details',args=[self.branch.pk])
        post=dict(self.post(), action='save', **{'people-0-author':'Someone else'})
        with patch('admin_motivation.closing_details_views.baseline',return_value=self.baseline()):
            response=self.client.post(url,post)
        self.assertContains(response,'Сотрудник не найден')
        self.assertFalse(PeriodMotivationCoefficient.objects.exists())

    def test_employee_result_labels_keep_unassigned_amounts_and_edit_only_people(self):
        rows,_=self.baseline()
        rows += [dict(rows[0],author='—',before='20'),dict(rows[0],author='',author_status='unallocated',before='30'),dict(rows[0],kind='Продажи',author='Sales',before='40')]
        result=closing_details.impact(rows)
        self.assertEqual(result['before'],Decimal('190'))
        self.assertEqual([r['author_label'] for r in result['rows']],['Policy','Автор не указан','Нераспределённая премия','Sales'])
        self.assertEqual([r['can_edit_person'] for r in result['rows']],[True,False,False,False])
        url=reverse('motivation_closing_details',args=[self.branch.pk])
        with patch('admin_motivation.closing_details_views.baseline',return_value=(rows,self.run)):
            response=self.client.get(url,{'year':2025,'period':4})
        self.assertEqual(response.context['authors_by_kind']['Политики'],['Policy'])
        self.assertContains(response,'data-edit-person',count=1)
        self.assertContains(response,'data-author="Policy"')

    def test_employee_choices_are_scoped_and_blank_extra_row_is_ignored(self):
        url=reverse('motivation_closing_details',args=[self.branch.pk])
        rows,_=self.baseline()
        rows += [dict(rows[0],kind='Директора',author='Director'),dict(rows[0],branch='Other',author='Other')]
        with patch('admin_motivation.closing_details_views.baseline',return_value=(rows,self.run)):
            response=self.client.get(url,{'year':2025,'period':4})
            self.assertEqual(response.context['authors_by_kind'],{'Политики':['Policy'],'Директора':['Director']})
            choices=response.context['people'].forms[0].fields['author'].choices
            self.assertEqual(choices,[('','Выберите сотрудника'),('Policy','Policy')])
            post=dict(self.post(),action='preview',**{'people-0-author':'','people-0-coefficient':''})
            preview=self.client.post(url,post)
            self.assertEqual(preview.status_code,200)
            post.update({'people-0-kind':'Директора','people-0-author':'Policy','people-0-coefficient':'1.2'})
            self.assertEqual(self.client.post(url,post).status_code,400)

    def test_all_branch_baseline_uses_three_queries_and_validates_cached_run(self):
        from contextlib import nullcontext
        from django.core.cache import cache
        cache.clear()
        payload={'period_summary':{'columns':[{'label':'4 Квартал'}], 'rows':[{'subdivision':'Closing','author':'Policy','values':[50], 'remainder':10}]}}
        with patch('admin_motivation.services.reports_next.current_run',side_effect=lambda run:nullcontext()) as current, patch('admin_motivation.services.reports.load_report',return_value=payload) as load:
            first=closing_details.source_rows(self.run,4,'USD')
            second=closing_details.source_rows(self.run,4,'USD')
        self.assertEqual(first,second)
        self.assertEqual(load.call_count,3)
        self.assertEqual(current.call_count,2)
        self.assertTrue(all(call.args[1]['Subdivision']==[] for call in load.call_args_list))
        self.assertFalse(without_correction.get())

    def test_real_fund_reader_applies_personal_and_kind_values_once(self):
        from contextlib import nullcontext
        from unittest.mock import Mock
        from .services import premium_distribution as distribution
        self.confirm()
        with patch('admin_motivation.services.reports_next.current_run',side_effect=lambda run:nullcontext()), patch('admin_motivation.services.reports.connect_database',return_value=(Mock(),Mock())), patch('admin_motivation.services.reports.fetch',return_value=[self.row()]), patch('admin_motivation.services.closing_coefficient.correction_mode',Mock(get=Mock(return_value='period-unified-v1'))):
            funds,rows=distribution.read_source(self.run,self.branch,4,'USD')
        by_kind={f['kind']:Decimal(f['amount']) for f in funds}
        self.assertEqual(by_kind['Политики'],65)
        self.assertEqual(by_kind['Продажи'],40)

    def test_dialog_allows_own_portal_frame_without_changing_other_pages(self):
        url=reverse('motivation_closing_details',args=[self.branch.pk])
        with patch('admin_motivation.closing_details_views.baseline',return_value=self.baseline()):
            response=self.client.get(url,{'year':2025,'period':4})
            self.assertEqual(response.headers['X-Frame-Options'],'SAMEORIGIN')
            post=self.client.post(url,dict(self.post(),action='preview'))
            self.assertEqual(post.headers['X-Frame-Options'],'SAMEORIGIN')
        self.assertEqual(self.client.get(reverse('motivation_distributions')).headers['X-Frame-Options'],'DENY')
