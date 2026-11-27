from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from . import test_distribution_stages as fixture
from .models import DirectorEmployee,Subdivision,Chanel
from .services import premium_distribution as service
from .services.closing_coefficient import confirm
from .services.distribution_shared import pools,minimum,upgrade,POLICY


class SharedDistributionTests(TestCase):
    def setUp(self):
        fixture.ClosingWorkflowTests.setUp(self)
        self.director=DirectorEmployee.objects.create(full_name='Branch director')
        self.director.subdivisions.add(self.branch)
        self.funds=[dict(kind=kind,payment='quarter',amount='100.00') for kind in service.KINDS]
        self.rows=[dict(id=kind,kind=kind,payment='quarter',author=('Policy' if kind=='Политики' else 'Branch director' if kind=='Директора' else 'Sales'),original='100.00',amount='100.00',comment='') for kind in service.KINDS]

    def batch(self,period=4):
        confirm(self.ceo,self.branch,2025,period,1)
        with patch.object(service,'read_source',return_value=(deepcopy(self.funds),deepcopy(self.rows))):
            return service.create(self.admin,self.run,self.branch,period,'USD')

    def changes(self,batch):
        return {r['id']:dict(amount=r['amount'],comment=r['comment']) for r in batch.rows}

    def edit(self,batch,kind,amount,action='save',payment='quarter'):
        changes=self.changes(batch)
        row=next(r for r in batch.rows if r['kind']==kind and r['payment']==payment)
        changes[row['id']]['amount']=str(amount)
        return service.update(self.admin,batch.pk,batch.revision,changes,action=action)

    def balance(self,batch,payment='quarter'):
        return next(p for p in pools(batch.funds,batch.rows) if p['payment']==payment)['remaining']

    def fill_sales(self,batch):
        return self.edit(batch,'Продажи',100,action='go_policies')

    def test_cross_kind_balance_and_completion_from_any_tab(self):
        batch=self.batch();self.assertEqual(batch.source['workflow']['rebalance_policy'],POLICY)
        self.assertEqual({r['kind'] for r in batch.rows},set(service.KINDS))
        batch=self.fill_sales(batch);self.assertEqual(self.balance(batch),0)
        batch=self.edit(batch,'Политики',80,action='go_directors');self.assertEqual(self.balance(batch),20)
        batch=self.edit(batch,'Директора',110,action='go_sales');self.assertEqual(self.balance(batch),10)
        batch=self.edit(batch,'Продажи',110,action='complete')
        self.assertEqual(batch.state,'completed');self.assertEqual(self.balance(batch),0)
        self.assertTrue(all(f['amount']=='100.00' for f in batch.funds))
        self.assertEqual(sum(Decimal(r['amount']) for r in batch.rows),300)
        response=self.client.get(reverse('motivation_distribution_detail',args=[batch.pk])+'?export=xlsx')
        from openpyxl import load_workbook
        from io import BytesIO
        book=load_workbook(BytesIO(b''.join(response.streaming_content)))
        self.assertIn('Общий остаток',book.sheetnames);book.close()

    def test_personal_floor_cannot_reset_by_saving_or_tab_switch(self):
        batch=self.fill_sales(self.batch())
        batch=self.edit(batch,'Политики',80)
        with self.assertRaises(ValidationError):self.edit(batch,'Политики',60)
        batch.refresh_from_db();self.assertEqual(next(r for r in batch.rows if r['kind']=='Политики')['original'],'100.00')
        batch=self.edit(batch,'Политики',75,action='go_directors')
        with self.assertRaises(ValidationError):self.edit(batch,'Директора','74.99')
        self.assertEqual(minimum(dict(kind='Политики',author='P',original='.01')),Decimal('.01'))

    def test_payment_pools_are_independent_and_no_overspending(self):
        self.funds=[dict(kind=k,payment=p,amount=str(amount)) for k in service.KINDS for p,amount in [('annual',100),('catchup',20)]]
        self.rows=[dict(id=k+p,kind=k,payment=p,author=k,original=str(amount),amount=str(amount),comment='') for k in service.KINDS for p,amount in [('annual',100),('catchup',20)]]
        batch=self.batch(period=5)
        batch=self.edit(batch,'Продажи',100,payment='annual')
        batch=self.edit(batch,'Продажи',20,payment='catchup',action='go_policies')
        batch=self.edit(batch,'Политики',80,payment='annual',action='go_sales')
        with self.assertRaises(ValidationError):self.edit(batch,'Продажи',21,payment='catchup')
        with self.assertRaises(ValidationError):service.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='complete')
        self.assertEqual(self.balance(batch,'annual'),20);self.assertEqual(self.balance(batch,'catchup'),0)

    def test_tampering_other_tab_and_original_fields_is_ignored(self):
        batch=self.batch();changes=self.changes(batch)
        changes['Политики'].update(amount='0',original='0',minimum='0')
        batch=service.update(self.admin,batch.pk,batch.revision,changes)
        self.assertEqual(next(r for r in batch.rows if r['kind']=='Политики')['amount'],'100.00')
        with self.assertRaises(ValidationError):service.update(self.admin,batch.pk,batch.revision-1,self.changes(batch))
        with self.assertRaises(PermissionDenied):service.update(self.finance,batch.pk,batch.revision,self.changes(batch))

    def test_director_addition_scoped_to_branch_and_reports_include_allocation(self):
        batch=self.fill_sales(self.batch());batch=self.edit(batch,'Политики',75,action='go_directors')
        other=DirectorEmployee.objects.create(full_name='Another director');other.subdivisions.add(self.branch)
        foreign=DirectorEmployee.objects.create(full_name='Foreign director')
        addition=dict(kind='Директора',director=foreign.pk,payment='quarter')
        with self.assertRaises(ValidationError):service.update(self.admin,batch.pk,batch.revision,self.changes(batch),addition=addition)
        addition['director']=other.pk
        batch=service.update(self.admin,batch.pk,batch.revision,self.changes(batch),addition=addition)
        added=next(r for r in batch.rows if r.get('director_id')==other.pk)
        changes=self.changes(batch);changes[added['id']]['amount']='25'
        batch=service.update(self.admin,batch.pk,batch.revision,changes,action='complete')
        from .services.distribution_reporting import apply
        report=[];apply(report,self.run,'Директора',{'Currency':'USD','Subdivision':['Closing']},['4'])
        self.assertEqual(sum(r['distributed_total'] for r in report),125)

    def test_ui_shows_three_tabs_pool_and_minimum_without_discarding_changes(self):
        batch=self.fill_sales(self.batch())
        response=self.client.get(reverse('motivation_distribution_detail',args=[batch.pk]))
        self.assertContains(response,'go_directors');self.assertContains(response,'data-pool-payment="quarter"')
        self.assertContains(response,'data-minimum="75.00"')
        self.assertContains(response,'Общий фонд подразделения')

    def test_legacy_upgrade_is_explicit_and_keeps_frozen_originals(self):
        batch=self.batch();batch.source['workflow'].pop('rebalance_policy')
        batch.funds=[f for f in batch.funds if f['kind']!='Директора'];batch.rows=[r for r in batch.rows if r['kind']!='Директора'];batch.save()
        with patch.object(service,'read_source',return_value=(deepcopy(self.funds),deepcopy(self.rows))):
            batch=upgrade(self.admin,batch.pk,batch.revision)
        self.assertEqual(batch.source['workflow']['rebalance_policy'],POLICY)
        self.assertEqual(next(r for r in batch.rows if r['kind']=='Политики')['original'],'100.00')
        self.assertEqual(next(r for r in batch.rows if r['kind']=='Директора')['amount'],'100.00')

    def test_http_tab_switch_preserves_policy_reduction_and_director_access(self):
        batch=self.fill_sales(self.batch())
        url=reverse('motivation_distribution_detail',args=[batch.pk])
        policy=next(r for r in batch.rows if r['kind']=='Политики')
        response=self.client.post(url,{'revision':batch.revision,'action':'go_sales','amount_'+policy['id']:'75','comment_'+policy['id']:'25 to sales'})
        self.assertEqual(response.status_code,302)
        batch.refresh_from_db();self.assertEqual(batch.source['workflow']['stage'],'sales')
        self.assertEqual(self.balance(batch),25)
        response=self.client.get(url)
        self.assertContains(response,'data-fixed-allocated="175.00"')
        from django.contrib.auth.models import User,Group
        from base.access import BRANCH_DIRECTOR
        from base.models import PortalReportGroup
        actor=User.objects.create_user('shared-branch');actor.groups.add(Group.objects.get(name=BRANCH_DIRECTOR))
        scope=Group.objects.create(name='Shared branch scope')
        entry=PortalReportGroup.objects.create(group=scope,portal_role=BRANCH_DIRECTOR);entry.subdivisions.add(self.branch);actor.groups.add(scope)
        from .models import TypeCoeff
        TypeCoeff.objects.get_or_create(type_coeff_name='Политики')
        self.client.force_login(actor)
        self.assertEqual(self.client.get(url).status_code,200)
        other=Subdivision.objects.create(subdivision_key='Elsewhere',subdivision_name='Elsewhere',subdivision_global='Elsewhere',chanel=self.branch.chanel)
        from .models import PremiumDistribution
        outside=PremiumDistribution.objects.create(subdivision=other,execution=self.run,year=2025,period=4,currency='USD')
        self.assertEqual(self.client.get(reverse('motivation_distribution_detail',args=[outside.pk])).status_code,403)

    def test_invalid_amount_rerenders_with_errors_and_no_write(self):
        batch=self.fill_sales(self.batch());policy=next(r for r in batch.rows if r['kind']=='Политики')
        response=self.client.post(reverse('motivation_distribution_detail',args=[batch.pk]),{'revision':batch.revision,'action':'save','amount_'+policy['id']:'bad'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'точностью до двух знаков')
        batch.refresh_from_db();self.assertEqual(next(r for r in batch.rows if r['kind']=='Политики')['amount'],'100.00')

    def test_negative_source_adjustments_are_preserved_and_cannot_be_changed(self):
        self.funds[0]['amount']='-10.00'
        self.rows[0].update(author='alice',original='-10.00',amount='-10.00')
        batch=self.batch()
        negative=next(r for r in batch.rows if Decimal(r['original'])<0)
        self.assertEqual(Decimal(negative['amount']),-10)
        self.assertEqual(self.balance(batch),0)
        changes=self.changes(batch);changes[negative['id']]['amount']='0'
        with self.assertRaises(ValidationError):service.update(self.admin,batch.pk,batch.revision,changes)
