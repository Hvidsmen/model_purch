from django.test import TestCase
from django.urls import reverse
from . import test_distribution_stages as fixtures
from .models import PeriodMotivationCoefficient, Subdivision


class RowConfirmationTests(TestCase):
    setUp=fixtures.ClosingWorkflowTests.setUp
    create=fixtures.ClosingWorkflowTests.create

    def post(self,**values):
        return self.client.post(reverse('motivation_distributions'),dict(action='confirm_row',subdivision=self.branch.pk,year=2025,period=4,revision=0,coefficient='1.25',**values))

    def test_single_branch_confirm_preserves_other_branches_and_rejects_stale_version(self):
        self.client.force_login(self.ceo)
        other=Subdivision.objects.create(subdivision_key='Row Other',subdivision_name='Other',subdivision_global='Other',chanel=self.branch.chanel)
        response=self.post()
        self.assertEqual(response.status_code,200)
        row=PeriodMotivationCoefficient.objects.get()
        self.assertEqual(str(row.coefficient),'1.2500')
        self.assertEqual(row.subdivision_id,self.branch.pk)
        self.assertFalse(PeriodMotivationCoefficient.objects.filter(subdivision=other).exists())
        self.assertEqual(self.post().status_code,400)
        row.refresh_from_db();self.assertEqual(row.revision,1)

    def test_finance_cannot_confirm_and_frozen_funds_cannot_change(self):
        self.client.force_login(self.finance)
        self.assertEqual(self.post().status_code,403)
        batch=self.create()
        self.client.force_login(self.ceo)
        values={'action':'confirm_row','subdivision':self.branch.pk,'year':2025,'period':4,'revision':1,'coefficient':'2'}
        response=self.client.post(reverse('motivation_distributions'),values)
        self.assertEqual(response.status_code,400)
        self.assertIn('зафиксированы',response.json()['error'])
        self.assertEqual(PeriodMotivationCoefficient.objects.get().coefficient,1)
