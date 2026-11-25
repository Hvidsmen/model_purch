from copy import deepcopy
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from .test_distribution_stages import ClosingWorkflowTests
from .services import premium_distribution as distribution


class DistributionNavigationTests(TestCase):
    setUp=ClosingWorkflowTests.setUp
    create=ClosingWorkflowTests.create
    changes=ClosingWorkflowTests.changes

    def test_round_trip_saves_politicians_and_invalidates_changed_sales(self):
        batch=self.create()
        batch=distribution.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='sales_complete')
        changes=self.changes(batch);changes['pol'].update(amount='45',comment='Черновик ребаланса')
        batch=distribution.update(self.admin,batch.pk,batch.revision,changes,action='return_sales')
        self.assertEqual(batch.source['workflow']['stage'],'sales')
        self.assertTrue(batch.source['workflow']['sales_at'])
        policy=deepcopy(batch.rows[-1])
        changes=self.changes(batch);changes[batch.rows[0]['id']]['weight']='2'
        batch=distribution.update(self.admin,batch.pk,batch.revision,changes,action='save')
        self.assertNotIn('sales_at',batch.source['workflow'])
        self.assertEqual(batch.rows[-1],policy)
        changes={r['id']:{'amount':r['amount'],'comment':r['comment'],'weight':r.get('weight','0')} for r in batch.rows}
        batch=distribution.update(self.admin,batch.pk,batch.revision,changes,action='sales_complete')
        self.assertEqual(batch.rows[-1],policy)
        self.assertTrue(batch.source['workflow']['sales_at'])
        self.assertEqual(sum(Decimal(r['amount']) for r in batch.rows if r['kind']=='Продажи'),100)
        changes=self.changes(batch);changes['pol']['amount']='50'
        batch=distribution.update(self.admin,batch.pk,batch.revision,changes,action='complete')
        with self.assertRaises(ValidationError):distribution.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='return_sales')

    def test_navigation_posts_save_visible_inputs_and_keep_revision_checks(self):
        batch=self.create();url=reverse('motivation_distribution_detail',args=[batch.pk])
        response=self.client.get(url)
        self.assertContains(response,'aria-label="Разделы распределения"')
        data={'revision':batch.revision,'action':'sales_complete'}
        data.update({'weight_'+r['id']:'1' for r in batch.rows if r['kind']=='Продажи'})
        self.assertEqual(self.client.post(url,data).status_code,302)
        batch.refresh_from_db();revision=batch.revision
        response=self.client.post(url,{'revision':revision,'action':'return_sales','amount_pol':'48','comment_pol':'Сохранить черновик'})
        self.assertEqual(response.status_code,302);batch.refresh_from_db()
        self.assertEqual(batch.rows[-1]['amount'],'48.00')
        self.assertEqual(batch.source['workflow']['stage'],'sales')
        response=self.client.post(url,{'revision':revision,'action':'sales_complete'})
        self.assertEqual(response.status_code,200);batch.refresh_from_db()
        self.assertEqual(batch.source['workflow']['stage'],'sales')

    def test_cannot_complete_after_forged_unconfirmed_navigation(self):
        batch=self.create();batch.source['workflow']['stage']='policies';batch.save()
        with self.assertRaises(ValidationError):distribution.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='complete')
