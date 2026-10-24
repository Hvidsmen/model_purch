from datetime import date
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.test import TestCase, Client
from django.urls import reverse
from base.access import ADMIN, MODELER, FINANCIER, DIRECTOR, MOTIVATION_VIEWER, can_access, role_for
from base.models import UserProjectAccess
from .models import GlobalCoeffVersion, GlobalCoeff, MotivationReview, MotivationReviewEvent
from .services import review_workflow as workflow
from .services.approval import review_approval, approve_review
from .services.versions import create_version, apply_to_subdivisions


class ReviewWorkflowTests(TestCase):
    def setUp(self):
        from .test_sales_plans import SalesPlanTests
        fixture = SalesPlanTests()
        fixture.setUp()
        fixture.load()
        self.fixture = fixture
        self.plan = fixture.scenario
        self.first = fixture.version
        approve_review(review_approval(self.first)['token'])
        self.version = create_version(date(2026, 1, 1), source_version=self.first.pk)
        self.version.coefficients.update(motivation_coeff=.12)
        self.version.refresh_from_db()
        self.users = {}
        for index, role in enumerate((ADMIN, MODELER, FINANCIER, DIRECTOR, MOTIVATION_VIEWER)):
            user = User.objects.create_user(f'review-{index}')
            UserProjectAccess.objects.create(user=user, project='motivation', role=role)
            self.users[role] = user

    def submit(self):
        self.version.refresh_from_db()
        return workflow.submit(self.users[MODELER], self.version.pk, self.plan.pk, self.version.revision, 'Модель на проверку')

    def client_as(self, role):
        client = Client()
        client.force_login(self.users[role])
        return client

    def test_full_route_and_immutable_audit(self):
        review = self.submit()
        self.version.refresh_from_db()
        self.assertFalse(self.version.is_editable)
        self.assertEqual(self.version.status, 'finance_review')
        self.assertTrue(review.changes)
        with self.assertRaises(ValidationError):
            self.version.coefficients.update(motivation_coeff=.5)
        with self.assertRaises(ValidationError):
            create_version(date(2027, 1, 1), source_version=self.version.pk)
        with self.assertRaises(ValidationError):
            apply_to_subdivisions(self.version, self.fixture.subs, user=self.users[ADMIN])
        with self.assertRaises(PermissionDenied):
            workflow.decide(self.users[DIRECTOR], review.pk, 'approve')
        with self.assertRaises(PermissionDenied):
            workflow.decide(self.users[MODELER], review.pk, 'agree')
        workflow.decide(self.users[FINANCIER], review.pk, 'agree', 'Премия проверена')
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'director_review')
        with self.assertRaises(PermissionDenied):
            workflow.decide(self.users[FINANCIER], review.pk, 'approve')
        workflow.decide(self.users[DIRECTOR], review.pk, 'approve', 'Утверждаю')
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'approved')
        self.assertEqual(list(review.events.values_list('action', flat=True)), ['submitted', 'agreed', 'approved'])
        self.assertEqual(self.version.approval.summary['decision']['route'], 'finance_director')
        with self.assertRaises(ValidationError):
            workflow.decide(self.users[DIRECTOR], review.pk, 'approve')
        with self.assertRaises(ValidationError):
            MotivationReview.objects.filter(pk=review.pk).update(comment='overwrite')
        with self.assertRaises(ValidationError):
            MotivationReviewEvent.objects.filter(review=review).delete()
        event = review.events.first()
        with self.assertRaises(ValidationError):
            event.save()

    def test_return_and_resubmit_restarts_finance(self):
        review = self.submit()
        workflow.decide(self.users[FINANCIER], review.pk, 'agree')
        with self.assertRaises(ValidationError):
            workflow.decide(self.users[DIRECTOR], review.pk, 'return')
        workflow.decide(self.users[DIRECTOR], review.pk, 'return', 'Перепроверить ставки')
        self.version.refresh_from_db()
        self.assertTrue(self.version.is_editable)
        self.version.coefficients.update(motivation_coeff=.13)
        next_review = self.submit()
        next_review.version.refresh_from_db()
        self.assertEqual(next_review.version.status, 'finance_review')
        self.assertNotEqual(review.snapshot, next_review.snapshot)
        with self.assertRaises(ValidationError):
            workflow.decide(self.users[DIRECTOR], review.pk, 'approve')
        self.assertEqual(review.events.last().comment, 'Перепроверить ставки')

    def test_changed_source_invalidates_signoff_and_can_be_returned(self):
        review = self.submit()
        line = self.plan.lines.first()
        line.amount_usd += 1
        line.save()
        with self.assertRaisesMessage(ValidationError, 'изменились после отправки'):
            workflow.decide(self.users[FINANCIER], review.pk, 'agree')
        workflow.decide(self.users[FINANCIER], review.pk, 'return', 'План обновлён')
        self.version.refresh_from_db()
        self.assertTrue(self.version.is_editable)

    def test_admin_bypass_requires_reason_and_audits(self):
        admin = self.users[ADMIN]
        token = review_approval(self.version)['token']
        with self.assertRaisesMessage(ValidationError, 'причину'):
            approve_review(token, actor=admin)
        approved = approve_review(token, actor=admin, override_reason='Пилот: проверено вручную')
        self.assertEqual(approved.approval.summary['decision']['reason'], 'Пилот: проверено вручную')
        self.assertEqual(approved.approval.summary['decision']['actor_id'], admin.pk)

    def test_admin_bypass_pending_uses_workflow_and_reason(self):
        review = self.submit()
        with self.assertRaisesMessage(ValidationError, 'причину'):
            workflow.decide(self.users[ADMIN], review.pk, 'override')
        workflow.decide(self.users[ADMIN], review.pk, 'override', 'Пилотный запуск')
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'approved')
        self.assertEqual(review.events.last().action, 'override')
        self.assertEqual(self.version.approval.summary['decision']['review_id'], review.pk)

    def test_modeler_scope_and_mutation_boundaries(self):
        user = self.users[MODELER]
        self.assertEqual(role_for(user, 'motivation'), MODELER)
        self.assertTrue(can_access(user, 'motivation_editor'))
        for target in ('motivation_admin', 'admin', 'purchases', 'dashboards'):
            self.assertFalse(can_access(user, target))
        client = self.client_as(MODELER)
        for route in ('loader_motive', 'motivation_calculations', 'motivation_subdivision_merge', 'portal_users'):
            self.assertEqual(client.get(reverse(route)).status_code, 403, route)
        data = {'action': 'approve', 'token': review_approval(self.version)['token'], 'reason': 'forged'}
        response = client.post(reverse('motivation_coefficient_comparison'), data, content_type='application/json')
        self.assertEqual(response.status_code, 403)
        row = self.version.coefficients.first()
        response = client.post(reverse('gb_act'), {'action_button': 'save_coeff', 'version': self.version.pk,
            f'global_coeff={row.pk}': '20%', f'select_var_calc={row.goods_id}': row.variation_calculate_id})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.motivation_coeff, .2)
        response = client.get(reverse('motivation_coefficient_balancing'))
        self.assertEqual(response.status_code, 200)

    def test_review_pages_and_role_decisions(self):
        client = self.client_as(MODELER)
        response = client.get(reverse('motivation_reviews'))
        self.assertContains(response, 'Отправить финансисту')
        self.version.refresh_from_db()
        response = client.post(reverse('motivation_reviews'), {'version': self.version.pk,
            'revision': self.version.revision, 'plan': self.plan.pk, 'comment': 'На проверку'})
        self.assertEqual(response.status_code, 302)
        review = MotivationReview.objects.get()
        url = reverse('motivation_review', args=[review.pk])
        response = self.client_as(FINANCIER).get(url)
        self.assertContains(response, 'Согласовать и передать генеральному директору')
        self.assertContains(response, 'Глобальная мотивация')
        self.assertNotContains(response, 'Утвердить весь набор')
        response = self.client_as(DIRECTOR).post(url, {'action': 'approve'})
        self.assertEqual(response.status_code, 403)
        response = self.client_as(FINANCIER).post(url, {'action': 'agree', 'comment': 'Проверено'})
        self.assertEqual(response.status_code, 302)
        response = self.client_as(DIRECTOR).get(url)
        self.assertContains(response, 'Утвердить весь набор')
        self.assertEqual(self.client_as(MOTIVATION_VIEWER).get(url).status_code, 403)
        # Reviewer roles cannot use the old mutation endpoints.
        for role in (FINANCIER, DIRECTOR):
            self.assertEqual(self.client_as(role).post(reverse('gb_act'), {'version': self.version.pk, 'action_button': 'fill_coeff'}).status_code, 403)

    def test_stale_revision_and_empty_plan_rejected(self):
        with self.assertRaises(ValidationError):
            workflow.submit(self.users[MODELER], self.version.pk, self.plan.pk, -1)
        self.plan.lines.all().delete()
        with self.assertRaisesMessage(ValidationError, 'непустой'):
            self.submit()

    def test_old_admin_endpoint_cannot_bypass_frozen_submission(self):
        review = self.submit()
        self.version.refresh_from_db()
        token = review_approval(self.version, self.plan)['token']
        with self.assertRaisesMessage(ValidationError, 'На проверке'):
            approve_review(token, actor=self.users[ADMIN], override_reason='Пилот')
        self.assertEqual(review.events.count(), 1)

    def test_raw_coefficient_changes_invalidate_finance_signoff(self):
        review = self.submit()
        row = self.version.coefficients.first()
        models.QuerySet.update(GlobalCoeff.objects.filter(pk=row.pk), motivation_coeff=.99)
        with self.assertRaisesMessage(ValidationError, 'изменились после отправки'):
            workflow.decide(self.users[FINANCIER], review.pk, 'agree')
        self.assertEqual(review.events.count(), 1)

    def test_send_cannot_be_replayed_with_previous_revision(self):
        self.version.refresh_from_db()
        revision = self.version.revision
        self.submit()
        with self.assertRaises(ValidationError):
            workflow.submit(self.users[MODELER], self.version.pk, self.plan.pk, revision)
        self.assertEqual(MotivationReview.objects.count(), 1)
