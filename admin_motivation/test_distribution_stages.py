from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth.models import User,Group
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from base.access import DIRECTOR,BRANCH_DIRECTOR,FINANCIER
from base.testing import authorize_admin
from .models import Chanel,Subdivision,SalesEmployee,MotivationExecution,PeriodMotivationCoefficient
from .services import premium_distribution as distribution
from .services.closing_coefficient import confirm,ReportMultipliers
from .services.distribution_stages import allocate


class ClosingWorkflowTests(TestCase):
    def setUp(self):
        self.admin=authorize_admin(self.client)
        channel=Chanel.objects.create(chanel_name='Закрытие этапы')
        self.branch=Subdivision.objects.create(subdivision_key='Closing',subdivision_name='Closing',subdivision_global='Closing',chanel=channel)
        self.run=MotivationExecution.objects.create(run_id=uuid4(),job_key=uuid4(),state='completed',started_at=timezone.now(),parameters={'fact_year':2025,'variant':'main','manager_correction_mode':'period-unified-v1'})
        self.ceo=User.objects.create_user('closing-ceo');self.ceo.groups.add(Group.objects.get(name=DIRECTOR))
        self.finance=User.objects.create_user('closing-finance');self.finance.groups.add(Group.objects.get(name=FINANCIER))
        self.people=[SalesEmployee.objects.create(full_name=name,subdivision=self.branch) for name in ('Alice','Bob','Carol')]
        self.funds=[{'kind':'Продажи','payment':'quarter','amount':'100.00'},{'kind':'Политики','payment':'quarter','amount':'50.00'}]
        self.rows=[{'id':'sales','kind':'Продажи','payment':'quarter','author':'Продажи','original':'100.00','amount':'100.00','comment':''},
                   {'id':'pol','kind':'Политики','payment':'quarter','author':'Policy','original':'50.00','amount':'50.00','comment':''}]

    def create(self,period=4):
        confirm(self.ceo,self.branch,2025,period,1)
        with patch.object(distribution,'read_source',return_value=(deepcopy(self.funds),deepcopy(self.rows))):
            return distribution.create(self.admin,self.run,self.branch,period,'USD')

    def changes(self,batch):
        return {r['id']:{'amount':r['amount'],'comment':r['comment'],'weight':'1'} for r in batch.rows}

    def test_confirmation_required_and_only_ceo_or_admin_can_confirm(self):
        with self.assertRaises(ValidationError):distribution.create(self.admin,self.run,self.branch,4,'USD')
        with self.assertRaises(PermissionDenied):confirm(self.finance,self.branch,2025,4,1)
        row=confirm(self.ceo,self.branch,2025,4,'1.2')
        self.assertEqual(row.coefficient,Decimal('1.2'))
        with self.assertRaises(ValidationError):confirm(self.ceo,self.branch,2025,4,'1.3',revision=0)
        row=confirm(self.ceo,self.branch,2025,4,'1.3',revision=row.revision)
        self.assertEqual(len(row.history),2)

    def test_common_factor_applies_once_to_four_types_without_changing_real_sales(self):
        confirm(self.ceo,self.branch,2025,4,'1.2')
        for kind in ('Политики','Продажи','Директора','Кураторы'):
            row={'Подразделение':'Closing','Год':2025,'КварталМотивации':'4 Квартал','ТипКоэф':kind,
                 'СуммаМотивацииUSD':100,'СуммаМотивацииUSDУчетПроцента':50,'Остаток':5,'O0':1000,'СуммаПланUSD':2000}
            reader=ReportMultipliers();first=reader.apply([row]);second=reader.apply(first)
            self.assertEqual(first,second)
            self.assertEqual(first[0]['СуммаМотивацииUSD'],120)
            self.assertEqual(first[0]['СуммаМотивацииUSDУчетПроцента'],60)
            self.assertEqual(first[0]['Остаток'],6)
            self.assertEqual(first[0]['O0'],1000)
            self.assertEqual(first[0]['СуммаПланUSD'],2000)
            self.assertEqual(row['СуммаМотивацииUSD'],100)

    def test_frozen_factor_cannot_change_after_funds_created(self):
        batch=self.create()
        with self.assertRaises(ValidationError):confirm(self.ceo,self.branch,2025,4,2,revision=1)
        self.assertEqual(batch.source['workflow']['coefficient'],'1.0000')
        self.assertEqual(batch.source['workflow']['stage'],'sales')

    def test_ordered_stages_rounding_and_finance_version(self):
        batch=self.create();changes=self.changes(batch)
        with self.assertRaises(ValidationError):distribution.update(self.admin,batch.pk,batch.revision,changes,action='complete')
        changes['pol']['amount']='0';changes['pol']['comment']='forged early change'
        batch=distribution.update(self.admin,batch.pk,batch.revision,changes,action='sales_complete')
        self.assertEqual(batch.source['workflow']['stage'],'policies')
        self.assertEqual(batch.rows[-1]['amount'],'50.00')
        sales=[r for r in batch.rows if r['kind']=='Продажи']
        self.assertEqual(sum(Decimal(r['amount']) for r in sales),Decimal(100))
        self.assertEqual(sorted(Decimal(r['amount']) for r in sales),[Decimal('33.33'),Decimal('33.33'),Decimal('33.34')])
        changes=self.changes(batch)
        for row in sales:changes[row['id']].update(amount='999999',weight='999')
        batch=distribution.update(self.admin,batch.pk,batch.revision,changes,action='complete')
        self.assertEqual(batch.state,'completed')
        self.assertEqual(sum(Decimal(r['amount']) for r in batch.rows if r['kind']=='Продажи'),Decimal(100))
        self.client.force_login(self.finance)
        response=self.client.get(reverse('motivation_distribution_detail',args=[batch.pk]),{'export':'xlsx'})
        self.assertEqual(response.status_code,200);response.close()
        self.assertEqual(batch.events.get(action='complete').snapshot['source']['workflow']['coefficient'],'1.0000')

    def test_sales_require_weights_and_cannot_cross_annual_catchup_funds(self):
        batch=self.create();changes=self.changes(batch)
        for row in changes.values():row['weight']='0'
        with self.assertRaises(ValidationError):distribution.update(self.admin,batch.pk,batch.revision,changes,action='sales_complete')
        funds=[{'kind':'Продажи','payment':'annual','amount':'1.00'},{'kind':'Продажи','payment':'catchup','amount':'2.00'}]
        rows=[{'id':str(i)+p,'kind':'Продажи','payment':p,'weight':str(i),'amount':'0'} for p in ('annual','catchup') for i in (0,1,2)]
        allocate(funds,rows,require_complete=True)
        for fund in funds:
            self.assertEqual(sum(Decimal(r['amount']) for r in rows if r['payment']==fund['payment']),Decimal(fund['amount']))
        self.assertTrue(all(Decimal(r['amount'])==0 for r in rows if r['weight']=='0'))

    def test_ceo_confirmation_post_and_stage_ui(self):
        self.client.force_login(self.ceo)
        response=self.client.post(reverse('motivation_distributions'),{'action':'confirm_coefficient','subdivision':self.branch.pk,'year':2025,'period':4,'coefficient':'1.1','revision':0})
        self.assertEqual(response.status_code,302)
        with patch.object(distribution,'read_source',return_value=(deepcopy(self.funds),deepcopy(self.rows))):batch=distribution.create(self.admin,self.run,self.branch,4,'USD')
        response=self.client.get(reverse('motivation_distribution_detail',args=[batch.pk]))
        self.assertContains(response,'Зафиксировать премии продаж → перейти к политикам')
        self.assertNotContains(response,'name="amount_pol"')
        self.assertContains(response,'name="weight_')

    def test_branch_director_has_stage_access_but_cannot_confirm(self):
        from base.models import PortalReportGroup
        from .models import TypeCoeff
        user=User.objects.create_user('closing-branch-director')
        user.groups.add(Group.objects.get(name=BRANCH_DIRECTOR))
        group=Group.objects.create(name='Closing branch scope')
        kind,_=TypeCoeff.objects.get_or_create(type_coeff_name='Директора')
        scope=PortalReportGroup.objects.create(group=group,employee_type=kind)
        scope.subdivisions.add(self.branch);user.groups.add(group)
        batch=self.create()
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('motivation_distribution_detail',args=[batch.pk])).status_code,200)
        self.assertEqual(self.client.post(reverse('motivation_distributions'),{'action':'confirm_coefficient'}).status_code,403)
        changes=self.changes(batch)
        batch=distribution.update(user,batch.pk,batch.revision,changes,action='sales_complete')
        self.assertEqual(batch.source['workflow']['stage'],'policies')

    def test_politician_rebalance_preserves_fund_with_optional_comments(self):
        self.rows.append({'id':'pol2','kind':'Политики','payment':'quarter','author':'Second Policy','original':'0.00','amount':'0.00','comment':''})
        batch=self.create()
        batch=distribution.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='sales_complete')
        changes=self.changes(batch);changes['pol']['amount']='40';changes['pol2']['amount']='10'
        changes['pol']['amount']='41'
        with self.assertRaisesMessage(ValidationError,'Нельзя распределить больше фонда'):
            distribution.update(self.admin,batch.pk,batch.revision,changes,action='save')
        changes['pol']['amount']='39'
        batch=distribution.update(self.admin,batch.pk,batch.revision,changes,action='save')
        with self.assertRaises(ValidationError):distribution.update(self.admin,batch.pk,batch.revision,changes,action='complete')
        changes['pol']['amount']='40'
        changes['pol']['comment']='Перераспределено'
        batch=distribution.update(self.admin,batch.pk,batch.revision,changes,action='complete')
        self.assertEqual(sum(Decimal(r['amount']) for r in batch.rows if r['kind']=='Политики'),50)
        self.assertEqual(next(r for r in batch.rows if r['id']=='pol2')['comment'],'')
        self.assertEqual(next(r for r in batch.rows if r['id']=='pol')['comment'],'Перераспределено')

    def test_annual_factor_is_separate_from_quarter_and_scales_accrual_once(self):
        confirm(self.ceo,self.branch,2025,4,'1.2');confirm(self.ceo,self.branch,2025,5,'0.5')
        rows=[{'Подразделение':'Closing','Год':2025,'КварталМотивации':'Год','СуммаМотивацииUSDУчетПроцента':100,'Остаток':20},
              {'Подразделение':'Closing','Год':2025,'Квартал':4,'AnnualAccrual':40}]
        result=ReportMultipliers().apply(rows)
        self.assertEqual(result[0]['СуммаМотивацииUSDУчетПроцента'],50)
        self.assertEqual(result[0]['Остаток'],10)
        self.assertEqual(result[1]['AnnualAccrual'],20)

    def test_report_loader_applies_the_factor_before_summary_and_export_data(self):
        from unittest.mock import MagicMock
        from .services.reports import load_report
        confirm(self.ceo,self.branch,2025,4,'1.2')
        source={'Подразделение':'Closing','АвторСделки':'Policy','Год':2025,'КварталМотивации':'4 Квартал',
                'СуммаМотивацииUSD':100,'СуммаМотивацииUSDУчетПроцента':50,'Остаток':0}
        with patch('admin_motivation.services.reports.connect_database',return_value=(MagicMock(),MagicMock())),patch('admin_motivation.services.reports.fetch',return_value=[source]):
            result=load_report('manager-detail',{'TypeCoeff_':'Политики','Subdivision':['Closing'],'DealAuthor':[],
                'gp_sales':[],'GPGoods':[],'Brend':[],'Years':['2025'],'Currency':'USD'},premium_period=['4'],summary_only=True)
        index=next(i for i,col in enumerate(result['period_summary']['columns']) if col['label']=='4 кв.')
        self.assertEqual(result['period_summary']['rows'][0]['values'][index],60)
        self.assertEqual(source['СуммаМотивацииUSDУчетПроцента'],50)

    def test_negative_fund_and_zero_weights_preserve_the_exact_total(self):
        rows=[{'id':str(i),'kind':'Продажи','payment':'quarter','weight':str(i),'amount':'0'} for i in (0,1,2)]
        allocate([{'kind':'Продажи','payment':'quarter','amount':'-1.01'}],rows,require_complete=True)
        self.assertEqual(sum(Decimal(r['amount']) for r in rows),Decimal('-1.01'))
        self.assertEqual(Decimal(rows[0]['amount']),0)

    def test_curator_multiplier_uses_its_physical_branch_after_alias_normalization(self):
        branch=Subdivision.all_objects.create(subdivision_key='Closing-TT',subdivision_name='TT',subdivision_global='TT',chanel=self.branch.chanel,merged_into=self.branch)
        confirm(self.ceo,branch,2025,4,'1.5')
        row={'Подразделение':'Closing','_source_subdivision':'Closing-TT','Год':2025,'КварталМотивации':'4 кв.',
             'СуммаМотивацииUSDУчетПроцента':100}
        self.assertEqual(ReportMultipliers(physical=True).apply([row])[0]['СуммаМотивацииUSDУчетПроцента'],150)
        self.assertEqual(ReportMultipliers().apply([row])[0]['СуммаМотивацииUSDУчетПроцента'],100)

    def test_existing_draft_can_explicitly_enter_the_first_stage_without_losing_history(self):
        from .services.distribution_stages import begin_legacy
        batch=self.create()
        batch.source.pop('workflow');batch.rows=deepcopy(self.rows);batch.rows[0]['amount']='80.00';batch.save()
        with self.assertRaises(ValidationError):begin_legacy(self.ceo,batch.pk,batch.revision,2,'')
        converted=begin_legacy(self.ceo,batch.pk,batch.revision,2,'Переход на этапы')
        self.assertEqual(converted.source['workflow']['stage'],'sales')
        self.assertEqual(converted.funds[0]['amount'],'200.00')
        self.assertEqual(converted.source['before_stages']['rows'][0]['amount'],'80.00')
        self.assertEqual(converted.events.count(),2)
        with self.assertRaises(ValidationError):begin_legacy(self.ceo,batch.pk,converted.revision,2,'Повтор')
