from datetime import date
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from . import test_distribution_stages as fixtures
from .models import PremiumDistribution, FaoPaymentClosure, FaoReview, Subdivision
from .services import fao_review as fao, premium_distribution as distribution
from .services.period_closure import mark_paid, ensure_open


class PaymentClosureTests(TestCase):
    setUp=fixtures.ClosingWorkflowTests.setUp

    def batch(self,period=2,year=2025,branch=None,state='completed'):
        batch=PremiumDistribution.objects.create(subdivision=branch or self.branch,execution=self.run,
            year=year,period=period,currency='USD',state=state,rows=self.rows,funds=self.funds,source={'run_id':str(self.run.run_id)})
        distribution.event(batch,self.admin,'complete' if state=='completed' else 'created')
        return batch

    def test_calendar_ended_quarter_is_open_until_fao_payment(self):
        with patch('admin_motivation.services.fao_review.timezone.localdate',return_value=date(2026,11,28)):
            review=fao.begin(self.finance,self.branch,2026,1,'Поздняя сверка')
            fao.validate_date(date(2026,3,31),review)
            with self.assertRaises(ValidationError):fao.validate_date(date(2026,4,1),review)
            fao.finish(self.finance,review.pk,review.revision)
            with self.assertRaises(ValidationError):fao.begin(self.finance,self.branch,2027,1,'Будущее')
        batch=self.batch(period=1,year=2026)
        mark_paid(self.finance,batch.pk,batch.revision)
        with self.assertRaisesMessage(ValidationError,'выплаты произведены'):
            # Error explains explicit FAO closure rather than calendar expiry.
            ensure_open(self.branch,2026,1)

    def test_payment_closes_only_branch_and_period_and_keeps_frozen_exports(self):
        batch=self.batch();record=mark_paid(self.finance,batch.pk,1)
        self.assertEqual(record.distribution_revision,1)
        self.assertEqual(record.actor_label,self.finance.username)
        self.assertIsNotNone(record.closed_at)
        batch.refresh_from_db();self.assertEqual(batch.revision,2)
        self.assertEqual(batch.events.first().action,'payments_made')
        for quarter in (1,3,4):ensure_open(self.branch,2025,quarter)
        other=Subdivision.objects.create(subdivision_key='OtherPaid',subdivision_name='Other',subdivision_global='Other',chanel=self.branch.chanel)
        ensure_open(other,2025,2)
        with self.assertRaises(ValidationError):fao.begin(self.finance,self.branch,2025,2,'Повторно')
        next_review=fao.begin(self.finance,self.branch,2025,3,'Коррекция открытого III квартала')
        fao.validate_date(date(2025,8,1),next_review)
        from .services.closing_return import return_to_ceo
        with self.assertRaises(ValidationError):return_to_ceo(self.ceo,batch.pk,2,0,'Вернуть')
        for action in ('reopen','save'):
            with self.assertRaises(ValidationError):distribution.update(self.admin,batch.pk,2,{},action=action,comment='reason')
        with self.assertRaises(ValidationError):distribution.retire(self.admin,batch.pk,2,'archive','reason')
        from .premium_distribution_views import snapshot_for
        for version in (1,2):
            snapshot,revision=snapshot_for(batch,self.finance,version)
            self.assertEqual(snapshot['rows'],self.rows)
            self.assertEqual(snapshot['state'],'completed')

    def test_only_fao_can_confirm_completed_current_revision(self):
        batch=self.batch(state='draft')
        with self.assertRaises(PermissionDenied):mark_paid(self.ceo,batch.pk,1)
        with self.assertRaises(ValidationError):mark_paid(self.finance,batch.pk,1)
        batch.state='completed';batch.save()
        with self.assertRaises(ValidationError):mark_paid(self.finance,batch.pk,0)
        self.assertFalse(FaoPaymentClosure.objects.exists())
        mark_paid(self.finance,batch.pk,1)
        with self.assertRaises(ValidationError):mark_paid(self.finance,batch.pk,1)
        self.assertEqual(FaoPaymentClosure.objects.count(),1)

    def test_active_review_prevents_payment_and_annual_close_covers_year(self):
        batch=self.batch(period=5)
        review=FaoReview.objects.create(subdivision=self.branch,year=2025,quarter=2,reason='check')
        with self.assertRaises(ValidationError):mark_paid(self.finance,batch.pk,1)
        review.state='completed';review.save()
        mark_paid(self.finance,batch.pk,1)
        for period in range(1,6):
            with self.assertRaises(ValidationError):ensure_open(self.branch,2025,period)
        ensure_open(self.branch,2024,2)

    def test_finance_checkbox_endpoint_and_readonly_paid_screen(self):
        batch=self.batch();url=reverse('motivation_distribution_paid',args=[batch.pk])
        self.client.force_login(self.finance)
        detail=reverse('motivation_distribution_detail',args=[batch.pk])
        response=self.client.get(detail)
        self.assertContains(response,'name="payments_made"')
        self.assertContains(response,'Подтвердить выплату и закрыть период')
        self.assertContains(self.client.get(detail+'?version=1'),'name="payments_made"')
        self.assertEqual(self.client.get(url).status_code,405)
        response=self.client.post(url,{'revision':1})
        self.assertEqual(response.status_code,302);self.assertFalse(FaoPaymentClosure.objects.exists())
        response=self.client.post(url,{'revision':1,'payments_made':'yes'})
        self.assertEqual(response.status_code,302);self.assertEqual(FaoPaymentClosure.objects.count(),1)
        response=self.client.get(detail)
        self.assertContains(response,'Выплаты произведены — период закрыт ФАО')
        self.assertNotContains(response,'name="payments_made"')
        self.assertEqual(self.client.get(detail+'?version=2&export=xlsx').status_code,200)
        self.client.force_login(self.ceo)
        self.assertEqual(self.client.post(url,{'revision':2,'payments_made':'yes'}).status_code,403)
