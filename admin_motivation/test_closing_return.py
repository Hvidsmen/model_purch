from copy import deepcopy
from unittest.mock import patch
from django.contrib.auth.models import User, Group
from base.access import BRANCH_DIRECTOR
from django.test import TestCase
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from . import test_distribution_stages as fixtures
from .models import PeriodMotivationCoefficient, PremiumDistribution
from .services import premium_distribution as distribution
from .services.closing_return import return_to_ceo, pending
from .services.closing_coefficient import confirm
from .services.distribution_workspace import registry


class ReturnToCoefficientTests(TestCase):
    setUp = fixtures.ClosingWorkflowTests.setUp
    create = fixtures.ClosingWorkflowTests.create
    changes = fixtures.ClosingWorkflowTests.changes

    def return_batch(self,batch):
        record=PeriodMotivationCoefficient.objects.get()
        return return_to_ceo(self.ceo,batch.pk,batch.revision,record.revision,'Изменение коэффициентов')

    def test_completed_snapshot_survives_and_new_allocation_requires_confirmation(self):
        batch=self.create()
        batch=distribution.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='sales_complete')
        batch=distribution.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='complete')
        old=deepcopy(batch.rows)
        completed=deepcopy(batch.events.get(action='complete').snapshot)
        batch=self.return_batch(batch)
        self.assertEqual(batch.state,'archived')
        self.assertEqual(batch.rows,old)
        self.assertEqual(batch.events.get(action='complete').snapshot,completed)
        record=PeriodMotivationCoefficient.objects.get()
        self.assertTrue(pending(record))
        with self.assertRaises(ValidationError):distribution.create(self.admin,self.run,self.branch,4,'USD')
        with self.assertRaises(ValidationError):distribution.update(self.admin,batch.pk,batch.revision,{},action='reopen',comment='attempt')
        confirm(self.ceo,self.branch,2025,4,2,revision=record.revision)
        fresh_funds=[dict(r,amount=str(distribution.cash(r['amount'])*2)) for r in self.funds]
        fresh_rows=[dict(r,original=str(distribution.cash(r['original'])*2),amount=str(distribution.cash(r['amount'])*2)) for r in self.rows]
        with patch.object(distribution,'read_source',return_value=(fresh_funds,fresh_rows)):
            fresh=distribution.create(self.admin,self.run,self.branch,4,'USD')
        self.assertNotEqual(fresh.pk,batch.pk)
        self.assertEqual(fresh.source['workflow']['stage'],'sales')
        self.assertTrue(all(r['amount']=='0.00' and r['weight']=='0' for r in fresh.rows if r['kind']=='Продажи'))
        self.assertEqual(fresh.rows[-1]['amount'],'100.00')
        self.assertEqual(fresh.source['previous_distribution']['rows'],old)
        self.assertEqual(PremiumDistribution.objects.filter(state='completed').count(),0)
        response=self.client.get(reverse('motivation_distribution_detail',args=[fresh.pk]))
        self.assertContains(response,'прежние назначения директора')
        self.assertContains(response,'Только для сравнения')

    def test_permissions_reason_stale_and_duplicate_return(self):
        batch=self.create()
        branch_director=User.objects.create_user('return-branch-director')
        branch_director.groups.add(Group.objects.get(name=BRANCH_DIRECTOR))
        for user in (self.finance,branch_director):
            with self.assertRaises(PermissionDenied):return_to_ceo(user,batch.pk,batch.revision,1,'reason')
        for revision,coefficient_revision,reason in [(99,1,'reason'),(1,99,'reason'),(1,1,'')]:
            with self.assertRaises(ValidationError):return_to_ceo(self.ceo,batch.pk,revision,coefficient_revision,reason)
        batch.refresh_from_db();self.assertEqual(batch.state,'draft')
        self.assertEqual(batch.events.count(),1)
        batch=self.return_batch(batch)
        with self.assertRaises(ValidationError):self.return_batch(batch)
        self.assertEqual(batch.events.count(),2)

    def test_registry_and_post_require_repeat_confirmation(self):
        batch=self.create()
        self.client.force_login(self.ceo)
        url=reverse('motivation_distributions')
        response=self.client.get(url,{'year':2025,'period':4})
        self.assertContains(response,'Вернуть к коэффициентам')
        response=self.client.post(url,dict(action='return_coefficients',batch=batch.pk,revision=batch.revision,coefficient_revision=1,reason='Новый коэффициент'))
        self.assertEqual(response.status_code,302)
        rows,_=registry(self.ceo,2025,4,[])
        row=next(r for r in rows if r['branch'].pk==self.branch.pk)
        self.assertTrue(row['awaiting_confirmation']);self.assertFalse(row['can_start'])
        self.assertIn('Возвращено',row['stage_label'])
        record=PeriodMotivationCoefficient.objects.get()
        confirm(self.ceo,self.branch,2025,4,1,revision=record.revision)
        rows,_=registry(self.ceo,2025,4,[])
        self.assertTrue(next(r for r in rows if r['branch'].pk==self.branch.pk)['can_start'])

    def test_annual_and_catchup_previous_amounts_are_separate(self):
        self.funds=[dict(r,payment=p) for r in self.funds for p in ('annual','catchup')]
        self.rows=[dict(r,id=r['id']+p,payment=p) for r in self.rows for p in ('annual','catchup')]
        batch=self.create(period=5)
        old=deepcopy(batch.rows)
        self.return_batch(batch)
        record=PeriodMotivationCoefficient.objects.get()
        confirm(self.ceo,self.branch,2025,5,1,revision=record.revision)
        with patch.object(distribution,'read_source',return_value=(deepcopy(self.funds),deepcopy(self.rows))):
            fresh=distribution.create(self.admin,self.run,self.branch,5,'RUB')
        previous=fresh.source['previous_distribution']
        self.assertEqual(previous['currency'],'USD')
        self.assertEqual(previous['rows'],old)
        self.assertEqual({r['payment'] for r in previous['rows']},{'annual','catchup'})
