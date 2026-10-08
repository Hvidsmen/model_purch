from datetime import date
from decimal import Decimal
from unittest.mock import Mock
from django.core.exceptions import ValidationError
from django.test import TestCase, Client
from django.urls import reverse
from .models import GlobalCoeffVersion, MotivationApproval, GlobalCoeff
from .services.approval import review_approval, approve_review
from .services.comparison import compare_plan, proposed_changes
from .services.versions import create_version, effective_version
from .services.sales_plans import calculate_plan
from .services.sql_export import export_motivation


class ComparisonTests(TestCase):
    def setUp(self):
        from .test_sales_plans import SalesPlanTests
        fixture = SalesPlanTests()
        fixture.setUp()
        self.first, self.plan, self.subs = fixture.version, fixture.scenario, fixture.subs
        fixture.cursor.fetchall.return_value = fixture.rows(date(2026, 1, 1)) + fixture.rows(date(2026, 8, 1)) + fixture.rows(date(2026, 11, 1))
        fixture.load()
        self.approve(self.first)

    def approve(self, version, **kwargs):
        return approve_review(review_approval(version, **kwargs)['token'], confirm_overwrite=True)

    def draft(self, on_date, value, overwrite=False):
        latest = GlobalCoeffVersion.objects.exclude(status='superseded').order_by('-pk').first()
        version = create_version(on_date, source_version=latest.pk, allow_overwrite=overwrite)
        version.coefficients.update(motivation_coeff=value)
        return version

    def timeline(self):
        july = self.draft(date(2026, 7, 1), .2)
        self.approve(july)
        october = self.draft(date(2026, 10, 1), .3)
        self.approve(october)
        return july, october

    def test_comparison_uses_dates_but_baseline_covers_entire_period(self):
        july, october = self.timeline()
        current = self.draft(date(2026, 9, 1), .4, True)
        result = compare_plan(self.plan, current, self.first)
        self.assertEqual(Decimal(result['variants']['approved']['total']), Decimal('1725'))
        self.assertEqual(Decimal(result['variants']['baseline']['total']), Decimal('675'))
        self.assertEqual(Decimal(result['variants']['current']['total']), Decimal('2025'))
        self.assertEqual(Decimal(result['deltas']['approved']['total']['amount']), Decimal('300'))
        self.assertEqual([v['id'] for v in result['replaced_versions']], [october.pk])
        self.assertEqual(MotivationApproval.objects.count(), 3)
        july.refresh_from_db(); october.refresh_from_db()
        self.assertEqual(july.status, 'approved'); self.assertEqual(october.status, 'approved')

    def test_unsaved_edits_preview_only_current_and_remain_unsaved(self):
        current = self.draft(date(2026, 9, 1), .4)
        row = current.coefficients.get(type_coeff__type_coeff_name='Политики', segment__segment_name='K0')
        changes, deleted = proposed_changes(current, None, {str(row.pk): '50%'})
        result = compare_plan(self.plan, current, self.first, changes=changes)
        self.assertEqual(Decimal(result['variants']['current']['total']), Decimal('1665'))
        self.assertEqual(Decimal(result['variants']['approved']['total']), Decimal('675'))
        row.refresh_from_db(); self.assertEqual(row.motivation_coeff, .4)
        review = review_approval(current, self.plan, self.first, changes=changes)
        self.assertTrue(review['unsaved'])
        approved = approve_review(review['token'])
        row.refresh_from_db(); self.assertEqual(row.motivation_coeff, .5)
        self.assertEqual(compare_plan(self.plan, approved, self.first)['variants']['approved']['total'], result['variants']['current']['total'])
        for model, field in [(approved.coefficients, 'motivation_coeff'), (approved.subdivision_coefficients, 'motivation_coeff')]:
            with self.assertRaises(ValidationError): model.update(**{field: .1})

    def test_earlier_replacement_requires_confirmation_and_keeps_baseline_history(self):
        july, october = self.timeline()
        current = self.draft(date(2026, 7, 1), .4, True)
        review = review_approval(current, self.plan, october)
        self.assertTrue(review['baseline_replaced'])
        with self.assertRaises(ValidationError): approve_review(review['token'])
        self.assertEqual(GlobalCoeffVersion.objects.filter(status='approved').count(), 3)
        approve_review(review['token'], True)
        for version in [july, october]:
            version.refresh_from_db()
            self.assertEqual(version.status, 'superseded')
            self.assertEqual(version.replaced_by_id, current.pk)
            self.assertEqual(version.coefficients.count(), 10)
            self.assertEqual(version.subdivision_coefficients.count(), 20)
        result = compare_plan(self.plan, current, october)
        self.assertEqual(Decimal(result['variants']['baseline']['total']), Decimal('2700'))
        self.assertEqual(effective_version(date(2026, 10, 1)), current)
        self.assertEqual(current.approval.replaced_ids, [july.pk, october.pk])

    def test_preview_subdivision_does_not_change_global_and_approval_freezes_whole_set(self):
        current = self.draft(date(2026, 9, 1), .4)
        row = current.subdivision_coefficients.get(subdivision=self.subs[0], type_coeff__type_coeff_name='Политики', segment__segment_name='K0')
        changes, _ = proposed_changes(current, self.subs[0], {str(row.pk): '.5'})
        review = review_approval(current, self.plan, self.first, self.subs[0], changes)
        self.assertEqual(Decimal(review['summary']['global']['variants']['current']['total']), Decimal('1650'))
        self.assertEqual(Decimal(review['summary']['subdivisions']['variants']['current']['total']), Decimal('1065'))
        approve_review(review['token'])
        row.refresh_from_db(); self.assertEqual(row.motivation_coeff, .5)
        current.refresh_from_db()
        self.assertFalse(current.is_editable)

    def test_stale_review_after_edit_rejects_without_archiving(self):
        current = self.draft(date(2026, 9, 1), .4)
        review = review_approval(current, self.plan)
        current.coefficients.update(motivation_coeff=.5)
        with self.assertRaisesMessage(ValidationError, 'изменились'): approve_review(review['token'])
        self.assertEqual(GlobalCoeffVersion.objects.get(pk=current.pk).status, 'draft')
        with self.assertRaises(ValidationError): approve_review(review['token'] + 'bad')

    def test_no_approval_and_minimums_are_scoped_to_variant(self):
        current = self.draft(date(2026, 9, 1), -.9)
        self.plan.lines.update(brand='Unknown')
        result = compare_plan(self.plan, current, self.first)
        self.assertEqual(Decimal(result['variants']['approved']['total']), Decimal('675'))
        self.assertEqual(Decimal(result['variants']['baseline']['total']), Decimal('675'))
        self.assertEqual(Decimal(result['variants']['current']['total']), Decimal('-2250'))
        self.assertGreater(result['variants']['current']['fallbacks'], 0)

    def test_plan_and_export_use_approved_only_and_keep_drafts_out(self):
        july, october = self.timeline()
        self.draft(date(2026, 9, 1), -.9, True)
        calculate_plan(self.plan.pk)
        self.assertEqual(sum(self.plan.lines.filter(subdivision='').values_list('total_usd', flat=True)), Decimal('1725'))
        connection, cursor = Mock(), Mock()
        versions = export_motivation(date(2026, 1, 1), Mock(return_value=(connection, cursor)))
        self.assertEqual([v.pk for v in versions], [self.first.pk, july.pk, october.pk])

    def test_api_rejects_foreign_cells_and_requires_csrf(self):
        current = self.draft(date(2026, 9, 1), .4)
        url = reverse('motivation_coefficient_comparison')
        data = {'action':'compare', 'version':current.pk, 'plan':self.plan.pk, 'baseline':self.first.pk}
        result = self.client.post(url, data, content_type='application/json')
        self.assertEqual(result.status_code, 200)
        data['changes'] = {str(self.first.coefficients.first().pk): '.3'}
        self.assertEqual(self.client.post(url, data, content_type='application/json').status_code, 400)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, data, content_type='application/json').status_code, 403)

    def test_date_and_lifecycle_cannot_be_changed_directly(self):
        with self.assertRaises(ValidationError): self.draft(date(2000, 1, 1), .1, True)
        with self.assertRaises(ValidationError): self.draft(date(2001, 1, 1), .1)
        self.first.status = 'draft'
        with self.assertRaises(ValidationError): self.first.save()
        with self.assertRaises(ValidationError): GlobalCoeffVersion.objects.filter(pk=self.first.pk).update(status='draft')
        with self.assertRaises(ValidationError): self.first.delete()

    def test_review_stales_when_plan_is_reloaded_or_reference_changes(self):
        current = self.draft(date(2026, 9, 1), .4)
        review = review_approval(current, self.plan)
        from django.utils import timezone
        type(self.plan).objects.filter(pk=self.plan.pk).update(loaded_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, 'изменились'):
            approve_review(review['token'])
        review = review_approval(current, self.plan)
        from .models import Goods
        Goods.objects.filter(pk=current.coefficients.first().goods_id).update(brand='Renamed')
        with self.assertRaisesMessage(ValidationError, 'изменились'):
            approve_review(review['token'])

    def test_approval_history_and_manager_coefficients_are_read_only(self):
        from .models import SubdivisionManagerCoeff, KindManagerCoeff
        current = self.draft(date(2026, 9, 1), .4)
        manager = SubdivisionManagerCoeff.objects.create(version=current, subdivision=self.subs[0], kind=KindManagerCoeff.objects.create(name='Year'), coeff=.8)
        current = self.approve(current)
        manager.coeff = .5
        with self.assertRaises(ValidationError): manager.save()
        with self.assertRaises(ValidationError): current.manager_coefficients.update(coeff=.1)
        approval = current.approval
        approval.summary = {}
        with self.assertRaises(ValidationError): approval.save()
        with self.assertRaises(ValidationError): MotivationApproval.objects.filter(pk=approval.pk).delete()


class UnapprovedComparisonTests(TestCase):
    def test_no_approved_versions_is_explicit_and_does_not_block_current_preview(self):
        from .test_sales_plans import SalesPlanTests
        fixture = SalesPlanTests(); fixture.setUp(); fixture.load()
        result = compare_plan(fixture.scenario, fixture.version)
        self.assertIn('error', result['variants']['approved'])
        self.assertEqual(Decimal(result['variants']['current']['total']), Decimal('225'))
        response = self.client.post(reverse('motivation_coefficient_comparison'),
            {'action': 'compare', 'version': fixture.version.pk, 'plan': fixture.scenario.pk, 'baseline': fixture.version.pk}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
