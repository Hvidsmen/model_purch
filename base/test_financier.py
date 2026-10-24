from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from .access import FINANCIER, ensure_roles, role_for, can_access
from .models import PortalReportGroup
from .report_access import politician_scope
from admin_motivation import test_shared_versions
from admin_motivation.models import GlobalCoeffVersion, DiscountSegment, MotivationExecution


class FinancierTests(TestCase):
    def setUp(self):
        test_shared_versions.SharedMotivationVersionTests.setUp(self)
        self.admin = User.objects.get(pk=self.client.session['_auth_user_id'])
        ensure_roles()
        self.user = User.objects.create_user('finance-read-only')
        self.user.groups.add(Group.objects.get(name=FINANCIER))
        self.client.force_login(self.user)
        from admin_motivation.services.approval import approve_review, review_approval
        DiscountSegment.objects.create(version=self.first, type_discount='MASS', o1=10)
        approve_review(review_approval(self.first)['token'])
        self.first.refresh_from_db()
        self.draft = GlobalCoeffVersion.objects.create(effective_from=date(2027, 1, 1), title='Private finance draft')
        DiscountSegment.objects.create(version=self.draft, type_discount='PRIVATE', o1=99)

    def test_assignment_and_inheritance_without_branch_scope(self):
        from .admin import PortalUserChangeForm
        from .group_views import ReportGroupForm
        from .user_views import UserForm
        for form in (PortalUserChangeForm(), ReportGroupForm()):
            self.assertIn((FINANCIER, FINANCIER), form.fields['portal_role'].choices)
        form = UserForm({'username': 'DOMAIN\\new-finance', 'portal_role': FINANCIER, 'is_active': 'on'}, actor=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(role_for(form.save()), FINANCIER)
        self.user.groups.clear()
        group = Group.objects.create(name='Finance inherited access')
        PortalReportGroup.objects.create(group=group, portal_role=FINANCIER)
        self.user.groups.add(group)
        user = User.objects.get(pk=self.user.pk)
        self.assertEqual(role_for(user), FINANCIER)
        self.assertTrue(can_access(user, 'motivation'))
        self.assertFalse(can_access(user, 'admin'))
        self.assertFalse(can_access(user, 'purchases'))
        self.assertIsNone(politician_scope(user))

    def test_all_branches_and_approved_segments_are_read_only(self):
        for sub in (self.sub, self.other):
            response = self.client.get(reverse('coeff_subdivisions_admin_motivation', args=[sub.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['selected_version'], self.first)
            self.assertEqual([v.pk for v in response.context['versions']], [self.first.pk])
            self.assertFalse(response.context['version_editable'])
            self.assertContains(response, 'Только просмотр')
            self.assertNotContains(response, 'Private finance draft')
            self.assertNotContains(response, 'href="' + reverse('global_coeff_admin_motivation'))
            self.assertNotContains(response, 'href="' + reverse('loader_motive'))
            self.assertNotContains(response, 'href="' + reverse('motivation_calculations'))
        page = self.client.get(reverse('motivation_discount_segments'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'MASS')
        self.assertNotContains(page, 'PRIVATE')
        self.assertNotContains(page, 'href="' + reverse('global_coeff_admin_motivation'))

    def test_draft_urls_and_stale_session_cannot_expose_unapproved_settings(self):
        routes = [('coeff_subdivisions_admin_motivation', [self.sub.pk]), ('motivation_discount_segments', [])]
        for name, args in routes:
            url = reverse(name, args=args)
            for version in (str(self.draft.pk), 'not-an-id', '999999'):
                self.assertEqual(self.client.get(url, {'version': version}).status_code, 404)
            session = self.client.session
            session['motivation_version'] = self.draft.pk
            session.save()
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context['selected_version'], self.first)

    def test_no_approved_version_shows_empty_state_and_no_unversioned_rows(self):
        from django.db.models import QuerySet
        QuerySet.update(GlobalCoeffVersion.objects.filter(pk=self.first.pk), status='superseded')
        for name, args in [('coeff_subdivisions_admin_motivation', [self.sub.pk]), ('motivation_discount_segments', [])]:
            page = self.client.get(reverse(name, args=args))
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, 'Утверждённых версий пока нет')
            self.assertNotContains(page, 'Private finance draft')

    def test_global_settings_administration_and_every_mutation_are_blocked(self):
        denied = [('global_coeff_admin_motivation', []), ('motivation_directors_global', []),
                  ('goods_matrix_admin_motivation', []), ('loader_motive', []), ('motivation_calculations', []),
                  ('motivation_subdivision_merge', []), ('motivation_director_directory', []),
                  ('motivation_copy_group', []), ('motivation_global_template', []),
                  ('motivation_director_coeff_import', []), ('scenario_list', []), ('portal_users', []), ('portal_user_groups', [])]
        for name, args in denied:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)
        self.assertEqual(self.client.get('/admin/').status_code, 403)
        writes = [('gb_act', []), ('sub_act', [self.sub.pk]), ('motivation_discount_segments', []),
                  ('motivation_coefficient_comparison', []), ('motivation_version_reopen', []),
                  ('motivation_bulk_formula', []), ('motivation_reports', [])]
        with patch('admin_motivation.services.sql_export.export_motivation') as export:
            for name, args in writes:
                for method in ('post', 'put', 'patch', 'delete'):
                    self.assertEqual(getattr(self.client, method)(reverse(name, args=args), {'version': self.first.pk}).status_code, 403)
            export.assert_not_called()
        self.local_coeff.refresh_from_db()
        self.assertEqual(self.local_coeff.motivation_coeff, .15)

    def test_all_reports_and_exports_have_no_pilot_or_identity_restrictions(self):
        from admin_motivation.services.reports import CATALOG
        page = self.client.get(reverse('motivation_reports'))
        for entry in CATALOG.values():
            self.assertContains(page, entry['title'])
        self.assertContains(page, 'Сводный отчёт по плану продаж')
        with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', return_value={'currency': 'USD', 'offset': 0, 'details': [], 'page': 1, 'pages': 1}) as load:
            for kind in CATALOG:
                page = self.client.get(reverse('motivation_report', args=[kind]), {'run': '1', 'quarter': '1 Квартал', 'type': 'Политики', 'year': '2024'})
                self.assertEqual(page.status_code, 200)
                self.assertNotIn('access_scope', load.call_args.kwargs)
                self.assertFalse(page.context['pilot_periods'])
                if kind == 'manager':
                    self.assertEqual(load.call_args.args[1]['qm'], '1 Квартал')
            load.return_value = {'currency': 'USD'}
            from io import BytesIO
            with patch('admin_motivation.services.report_excel.report_excel', return_value=BytesIO(b'xlsx')):
                page = self.client.get(reverse('motivation_report', args=['manager-detail']), {'export': 'xlsx', 'type': 'Политики', 'year': '2024'})
                self.assertEqual(page.status_code, 200)
        self.assertFalse(MotivationExecution.objects.exists())

    def test_home_and_preview_use_finance_landing_and_enforce_permissions(self):
        page = self.client.get(reverse('index_base'))
        self.assertContains(page, 'href="' + reverse('motivation_reports') + '"')
        self.assertNotContains(page, reverse('global_coeff_admin_motivation'))
        self.assertRedirects(self.client.get(reverse('index_admin_motivation')), reverse('motivation_reports'))
        self.client.force_login(self.admin)
        self.client.post(reverse('portal_user_preview'), {'user': self.user.pk})
        page = self.client.get(reverse('coeff_subdivisions_admin_motivation', args=[self.sub.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context['portal_financier'])
        self.assertEqual(self.client.get(reverse('global_coeff_admin_motivation')).status_code, 403)
        self.assertEqual(self.client.post(reverse('sub_act', args=[self.sub.pk])).status_code, 403)
