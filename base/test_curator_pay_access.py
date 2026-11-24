from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from admin_motivation.models import TypeCoeff
from admin_motivation.services.reports_next import restrict
from .access import DIRECTOR, POLITICIAN
from .models import MotivationReportAccess, PortalReportGroup
from .report_access import politician_scope, scoped_values


class CuratorPayAccessTests(TestCase):
    def setUp(self):
        from .test_branch_roles import BranchRoleTests
        fixture = BranchRoleTests()
        fixture.setUp()
        self.fixture = fixture
        self.curator_type, _ = TypeCoeff.objects.get_or_create(type_coeff_name='Кураторы')
        self.policy = User.objects.create_user('curator-pay-policy')
        self.policy.groups.add(Group.objects.get(name=POLITICIAN))
        MotivationReportAccess.objects.create(user=self.policy, subdivision=fixture.branches[0], deal_author='Own policy')
        self.users = [*fixture.users.values(), self.policy]

    def test_default_kinds_and_legacy_queries_exclude_curator_pay(self):
        for user in self.users:
            with self.subTest(user=user.username):
                scope = politician_scope(user)
                _, kinds, _ = restrict(user, QueryDict())
                self.assertNotIn('Кураторы', kinds)
                values = scoped_values('manager-detail', {'TypeCoeff_': 'Кураторы'}, scope)
                self.assertNotEqual(values['TypeCoeff_'], 'Кураторы')

    def test_explicit_curator_report_and_excel_are_denied(self):
        for user in self.users:
            self.client.force_login(user)
            for kind in ('mine', 'registry', 'detail'):
                for export in ('', 'xlsx'):
                    with self.subTest(user=user.username, kind=kind, export=export):
                        response = self.client.get(reverse('motivation_report_next', args=[kind]),
                                                   {'type': 'Кураторы', 'run': '1', 'export': export})
                        self.assertEqual(response.status_code, 403)

    def test_politician_group_cannot_grant_curator_pay(self):
        group = Group.objects.create(name='Curator pay forbidden group')
        mapping = PortalReportGroup.objects.create(group=group, employee_type=self.curator_type)
        mapping.subdivisions.add(self.fixture.branches[0])
        self.policy.groups.add(group)
        self.assertIsNone(politician_scope(self.policy))
        with self.assertRaises(PermissionDenied):
            restrict(self.policy, QueryDict())

    def test_administrator_keeps_curator_pay(self):
        self.assertIsNone(politician_scope(self.fixture.admin))
        self.assertIn('Кураторы', restrict(self.fixture.admin, QueryDict())[1])

    def test_general_director_keeps_curator_pay(self):
        user = User.objects.create_user('curator-pay-ceo')
        user.groups.add(Group.objects.get(name=DIRECTOR))
        self.assertIsNone(politician_scope(user))
        self.assertIn('Кураторы', restrict(user, QueryDict())[1])
