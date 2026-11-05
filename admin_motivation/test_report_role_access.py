from contextlib import nullcontext
from io import BytesIO
from uuid import uuid4
from unittest.mock import Mock, patch
from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from base.access import BRANCH_DIRECTOR, DEPUTY_DIRECTOR, POLITICIAN, SALES
from base.models import MotivationReportAccess
from .models import MotivationExecution
from .services import reports_next
from .services.manager_detail import manager_detail_report
from .test_manager_detail import detail_rows


class ReportRoleAccessTests(TestCase):
    def setUp(self):
        from base.test_branch_roles import BranchRoleTests
        fixture = BranchRoleTests(); fixture.setUp()
        self.fixture = fixture
        self.run = MotivationExecution.objects.create(run_id=uuid4(), job_key=uuid4(), state='completed', started_at=timezone.now(), completed_at=timezone.now(), parameters={'fact_year': 2026})
        self.url = lambda kind: reverse('motivation_report_next', args=[kind])
        self.policy = User.objects.create_user('policy-registry')
        self.policy.groups.add(Group.objects.get(name=POLITICIAN))
        MotivationReportAccess.objects.create(user=self.policy, subdivision=fixture.branches[0], deal_author='Own policy')

    def source(self, branch='Allowed-A', author='Alice'):
        return manager_detail_report([dict(detail_rows()[1], Подразделение=branch, АвторСделки=author)])

    def test_director_employee_report_and_export_validate_branch_and_employee(self):
        self.client.force_login(self.fixture.users[BRANCH_DIRECTOR])
        catalog = self.client.get(reverse('motivation_reports'))
        self.assertContains(catalog, 'Премия сотрудника')
        self.assertContains(catalog, 'Реестр премий к выплате')
        with patch('admin_motivation.services.reports_next.employee_authors', return_value=['Alice', 'Bob']), patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.services.reports_next.load_report', return_value=self.source()) as load:
            response = self.client.get(self.url('mine'), {'run': '1', 'type': 'Политики', 'subdivision': 'Allowed-A', 'author': 'Alice'})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '<h1>Премия сотрудника</h1>')
            self.assertEqual(load.call_args.args[1]['Subdivision'], ['Allowed-A'])
            self.assertEqual(load.call_args.args[1]['DealAuthor'], ['Alice'])
            self.assertEqual(response.context['employee_filters']['branches'], ['Allowed-A', 'Allowed-B'])
            exported = self.client.get(self.url('mine'), {'export': 'xlsx', 'type': 'Политики', 'subdivision': 'Allowed-A', 'author': 'Alice'})
            book = load_workbook(BytesIO(b''.join(exported.streaming_content)))
            self.assertEqual(book['Премии']['A1'].value, 'Премия сотрудника')
            book.close(); exported.close()
            before = load.call_count
            for overrides in ({'subdivision': 'Denied-C'}, {'author': 'Foreign'}, {'author': ['Alice', 'Bob']}):
                response = self.client.get(self.url('mine'), {'export': 'xlsx', 'type': 'Политики', 'subdivision': 'Allowed-A', 'author': 'Alice', **overrides})
                self.assertEqual(response.status_code, 403)
            self.assertEqual(load.call_count, before)
            self.assertEqual(self.client.get(self.url('mine'), {'run': '1', 'author': '', 'subdivision': 'Allowed-B'}).status_code, 200)
            self.assertContains(self.client.get(self.url('mine'), {'run': '1'}), 'Выберите сотрудника в доступном подразделении.')

    def test_director_and_deputy_registry_cannot_escape_assigned_branches(self):
        for role in (BRANCH_DIRECTOR, DEPUTY_DIRECTOR):
            self.client.force_login(self.fixture.users[role])
            with patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.services.reports_next.load_report', return_value=self.source()) as load:
                response = self.client.get(self.url('registry'), {'run': '1'})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(all(set(call.args[1]['Subdivision']) <= {'Allowed-A', 'Allowed-B'} for call in load.call_args_list))
                for export in ('', 'xlsx'):
                    denied = self.client.get(self.url('registry'), {'run': '1', 'export': export, 'subdivision': 'Denied-C'})
                    self.assertEqual(denied.status_code, 403)
            self.assertNotIn('executive', reports_next.available(self.fixture.users[role]))

    def test_deputy_can_choose_policies_but_director_motivation_is_own_only(self):
        user = self.fixture.users[DEPUTY_DIRECTOR]
        user.first_name, user.last_name = 'Иван', 'Иванов'; user.save()
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse('motivation_reports')), 'Моя премия/Премия сотрудников')
        # The own-director employee list is derived from trusted identity without DWH lookup.
        self.assertEqual(reports_next.employee_authors(user, 'Allowed-A', 'Директора', '2026'), ['Иванов Иван'])
        with patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.services.reports_next.load_report', return_value=self.source(author='Иванов Иван')) as load:
            response = self.client.get(self.url('mine'), {'run': '1', 'type': 'Директора', 'subdivision': 'Allowed-A'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(load.call_args.args[1]['DealAuthor'], ['Иванов Иван'])
            denied = self.client.get(self.url('mine'), {'export': 'xlsx', 'type': 'Директора', 'subdivision': 'Allowed-A', 'author': 'Other director'})
            self.assertEqual(denied.status_code, 403)

    def test_politician_registry_and_excel_force_own_identity(self):
        self.client.force_login(self.policy)
        self.assertContains(self.client.get(reverse('index_admin_motivation')), 'Реестр премий к выплате')
        with patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.services.reports_next.load_report', return_value=self.source(author='Own policy')) as load:
            for export in ('', 'xlsx'):
                response = self.client.get(self.url('registry'), {'run': '1', 'export': export, 'author': 'Other', 'subdivision': 'Denied-C', 'type': 'Политики'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(load.call_args.args[1]['DealAuthor'], ['Own policy'])
                self.assertEqual(load.call_args.args[1]['Subdivision'], ['Allowed-A'])
                if export:
                    book = load_workbook(BytesIO(b''.join(response.streaming_content)))
                    self.assertEqual(book['Политики']['B6'].value, 'Own policy')
                    book.close(); response.close()
            self.assertEqual(self.client.get(self.url('registry'), {'run': '1', 'type': 'Директора'}).status_code, 403)
        self.policy.motivation_report_access.delete()
        self.assertEqual(self.client.get(self.url('registry')).status_code, 403)

    def test_employee_lookup_is_bounded_to_branch_type_and_year(self):
        user = self.fixture.users[BRANCH_DIRECTOR]
        cursor, connection = Mock(), Mock()
        cursor.description = [('author',)]
        cursor.fetchmany.return_value = [('Alice',), ('Политики',), ('',)]
        with patch('admin_motivation.conns.connect_database', return_value=(connection, cursor)):
            self.assertEqual(reports_next.employee_authors(user, 'Allowed-A', 'Политики', '2026'), ['Alice'])
        sql, *args = cursor.execute.call_args.args
        self.assertIn('[Подразделение] IN (?)', sql)
        self.assertIn('Allowed-A', args)
        self.assertIn('Политики', args)
        self.assertIn('2026', args)
        connection.close.assert_called_once()
