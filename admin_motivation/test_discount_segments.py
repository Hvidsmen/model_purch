from datetime import date
from decimal import Decimal
from unittest.mock import Mock
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_test_case
from .models import DiscountSegment, GlobalCoeffVersion, GlobalCoeff, Goods, TypeCoeff, SegmentCoeff, VariationCalculate
from .services.versions import create_version, delete_draft
from .services.approval import approve_review, review_approval
from .services.sql_export import export_motivation
from .services.discount_segments import import_missing_versions, INSERT_SQL


class DiscountSegmentTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.first = GlobalCoeffVersion.objects.get(effective_from=date(2001, 1, 1))
        good = Goods.objects.create(goods_key='Discount test', planning_group_sales='Sales', group='Group', brand='Brand')
        kind, _ = TypeCoeff.objects.get_or_create(type_coeff_name='Политики')
        segment, _ = SegmentCoeff.objects.get_or_create(segment_name='K0')
        variation, _ = VariationCalculate.objects.get_or_create(variation_name='Группа планов продаж')
        GlobalCoeff.objects.create(version=self.first, goods=good, type_coeff=kind, segment=segment, variation_calculate=variation, motivation_coeff=.1, manager_coeff=1)

    def test_subdivision_navigation_is_populated_on_segments_and_directory(self):
        from .models import Subdivision, Chanel
        channel = Chanel.objects.create(chanel_name='Navigation')
        branch = Subdivision.objects.create(subdivision_key='NAV',subdivision_name='Navigation branch',subdivision_global='Navigation branch',chanel=channel)
        for route in ('motivation_discount_segments', 'motivation_director_directory'):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertContains(response, reverse('coeff_subdivisions_admin_motivation',args=[branch.pk]))
                self.assertContains(response, 'Navigation branch')
                self.assertNotContains(response, '<li>Подразделения не найдены</li>')

    def row(self, version=None, name='MASS', **values):
        return DiscountSegment.objects.create(version=version or self.first, type_discount=name, **{'o1': Decimal('10'), 'o2': Decimal('17'), 'o3': Decimal('19'), 'o4': Decimal('24'), **values})

    def test_shared_versions_copy_thresholds_and_keep_nulls(self):
        original = self.row()
        approved = approve_review(review_approval(self.first)['token'])
        draft = create_version(date(2026, 1, 1), source_version=approved.pk)
        copied = draft.discount_segments.get()
        self.assertIsNone(copied.o0)
        self.assertEqual(copied.o1, Decimal('10'))
        copied.o4 = Decimal('30')
        copied.save()
        original.refresh_from_db()
        self.assertEqual(original.o4, Decimal('24'))
        with self.assertRaises(ValidationError):
            original.save()
        with self.assertRaises(ValidationError):
            self.first.discount_segments.update(o4=40)
        delete_draft(draft.pk)
        self.assertFalse(DiscountSegment.objects.filter(version_id=draft.pk).exists())

    def test_threshold_validation_and_case_insensitive_identity(self):
        self.row()
        for values in ({'o2': 5}, {'o4': 101}, {'o0': -1}, {'o1': 'wrong'}, {'o2': Decimal('NaN')}):
            with self.assertRaises(ValidationError):
                self.row(name='INVALID', **values)
        with self.assertRaises(ValidationError):
            self.row(name=' mass ')
        row = self.row(name='NO_DISCOUNT', o1=None, o2=None, o3=None, o4=None)
        self.assertTrue(all(getattr(row, f'o{i}') is None for i in range(5)))

    def test_changed_threshold_invalidates_approval_review(self):
        row = self.row()
        review = review_approval(self.first)
        self.assertEqual(review['summary']['discount_segments'][0][2], '10.0000')
        row.o4 = 28
        row.save()
        with self.assertRaisesMessage(ValidationError, 'изменились'):
            approve_review(review['token'])

    def test_export_uses_approved_dates_decimal_values_and_nulls_atomically(self):
        self.row()
        approve_review(review_approval(self.first)['token'])
        draft = create_version(date(2026, 2, 3), source_version=self.first.pk)
        draft.discount_segments.update(o4=30)
        approve_review(review_approval(draft)['token'])
        create_version(date(2027, 1, 1), source_version=draft.pk)
        connection, cursor = Mock(), Mock()
        export_motivation(Mock(return_value=(connection, cursor)))
        rows = [call.args[1] for call in cursor.executemany.call_args_list if call.args[0] == INSERT_SQL]
        self.assertEqual([values[0][0] for values in rows], [date(2001, 1, 1), date(2026, 2, 3)])
        self.assertEqual(rows[0][0], (date(2001, 1, 1), 'MASS', None, Decimal(10), Decimal(17), Decimal(19), Decimal(24)))
        self.assertEqual(rows[1][0][-1], Decimal(30))
        self.assertTrue(any('DELETE FROM DataWH.motivation.SegmentMotivation WHERE Date_ >= ?' == call.args[0] for call in cursor.execute.call_args_list))
        connection.commit.assert_called_once()
        cursor.executemany.side_effect = RuntimeError('failed insert')
        connection.reset_mock()
        with self.assertRaises(RuntimeError):
            export_motivation(Mock(return_value=(connection, cursor)))
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()

    def test_import_fills_missing_versions_only_and_resolves_effective_dates(self):
        self.row(o4=25)
        draft = create_version(date(2026, 1, 1), source_version=self.first.pk)
        draft.discount_segments.all().delete()
        connection, cursor = Mock(), Mock()
        cursor.execute.return_value.fetchall.return_value = [
            (date(2001, 1, 1), 'MASS', None, 10, 17, 19, 24),
            (date(2025, 1, 1), 'MASS', None, 10, 20, 30, 40),
            (date(2025, 1, 1), 'PROJECT', None, None, None, None, None),
        ]
        self.assertEqual(import_missing_versions(Mock(return_value=(connection, cursor))), (1, 2))
        self.assertEqual(self.first.discount_segments.get().o4, Decimal(25))
        self.assertEqual(draft.discount_segments.get(type_discount='MASS').o4, Decimal(40))
        self.assertIsNone(draft.discount_segments.get(type_discount='PROJECT').o4)
        connection.close.assert_called_once()
        connection.commit.assert_not_called()
        self.assertTrue(cursor.execute.call_args.args[0].startswith('SELECT'))

    def test_import_rejects_bad_history_without_partial_fill(self):
        draft = create_version(date(2026, 1, 1), source_version=self.first.pk)
        connection, cursor = Mock(), Mock()
        cursor.execute.return_value.fetchall.return_value = [(date(2025, 1, 1), 'MASS', None, 10, 17, 19, 24)]
        with self.assertRaises(ValidationError):
            import_missing_versions(Mock(return_value=(connection, cursor)))
        self.assertFalse(DiscountSegment.objects.exists())
        cursor.execute.return_value.fetchall.return_value = [(date(2001, 1, 1), 'MASS', None, 10, 17, 19, 24), (date(2001, 1, 1), 'mass', None, 10, 17, 19, 24)]
        with self.assertRaises(ValidationError):
            import_missing_versions(Mock(return_value=(connection, cursor)))
        self.assertFalse(draft.discount_segments.exists())

    def test_web_saves_version_bound_nullable_fields_and_rejects_stale_revision(self):
        url = reverse('motivation_discount_segments') + f'?version={self.first.pk}'
        response = self.client.get(url)
        self.first.refresh_from_db()
        data = {'revision': self.first.revision, 'segments-TOTAL_FORMS': 1, 'segments-INITIAL_FORMS': 0, 'segments-MIN_NUM_FORMS': 0, 'segments-MAX_NUM_FORMS': 1000, 'segments-0-type_discount': 'MASS', 'segments-0-o0': '', 'segments-0-o1': '10', 'segments-0-o2': '17', 'segments-0-o3': '19', 'segments-0-o4': '24'}
        self.assertContains(response, 'Сегменты скидок')
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.first.discount_segments.get().o0)
        response = self.client.post(url, data)
        self.assertContains(response, 'Версия изменилась')

    def test_partial_approved_configuration_blocks_export_before_sql_changes(self):
        # Deliberately leave an approved version without local thresholds.
        self.row()
        approve_review(review_approval(self.first)['token'])
        draft = create_version(date(2026, 1, 1), source_version=self.first.pk)
        approve_review(review_approval(draft)['token'])
        from django.db import models
        models.QuerySet.delete(self.first.discount_segments.all())
        connector = Mock()
        with self.assertRaisesMessage(ValidationError, 'Не во всех'):
            export_motivation(connector)
        connector.assert_not_called()

    def test_non_admin_cannot_edit_or_import_discount_segments(self):
        from django.contrib.auth.models import User, Group
        from base.access import DIRECTOR
        user = User.objects.create_user('not-segment-admin')
        user.groups.add(Group.objects.get(name=DIRECTOR))
        self.client.force_login(user)
        url = reverse('motivation_discount_segments')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'action': 'import'}).status_code, 403)

    def test_segment_export_respects_shared_transaction_owner(self):
        self.row()
        approve_review(review_approval(self.first)['token'])
        connection, cursor = Mock(), Mock()
        export_motivation(Mock(return_value=(connection, cursor)), manage_connection=False)
        self.assertTrue(any(call.args[0] == INSERT_SQL for call in cursor.executemany.call_args_list))
        connection.commit.assert_not_called()
        connection.close.assert_not_called()

    def test_replaced_versions_are_not_exported(self):
        self.row()
        approve_review(review_approval(self.first)['token'])
        replacement = create_version(date(2001, 1, 1), source_version=self.first.pk, allow_overwrite=True)
        replacement.discount_segments.update(o4=50)
        approve_review(review_approval(replacement)['token'], confirm_overwrite=True)
        connection, cursor = Mock(), Mock()
        export_motivation(Mock(return_value=(connection, cursor)))
        inserts = [call.args[1] for call in cursor.executemany.call_args_list if call.args[0] == INSERT_SQL]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][0][-1], Decimal(50))
