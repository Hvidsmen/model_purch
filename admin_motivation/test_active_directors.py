import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from .services import active_directors as active
from .services.reports import bind_sql


class ActiveDirectorTests(SimpleTestCase):
    def test_archived_records_are_not_loaded_and_branch_binding_is_used(self):
        with patch.object(active, 'DirectorEmployee') as employee, patch.object(active, 'mapping', return_value={'Old': 'A'}):
            employee.subdivisions.through.objects.filter.return_value.values_list.return_value = [(1, 'DIR-1', 'Current', 'Old')]
            self.assertEqual(active.bindings(), [{'employee_id': 1, 'employee_code': 'DIR-1', 'full_name': 'Current', 'subdivision': 'A'}])
            employee.subdivisions.through.objects.filter.assert_called_once_with(directoremployee__is_archived=False)

    def test_export_excludes_archived_and_unbound_employees(self):
        def coefficient(employee_id, branch):
            return SimpleNamespace(employee_id=employee_id, subdivision=SimpleNamespace(subdivision_key=branch))
        current, removed, foreign = coefficient(1, 'A'), coefficient(2, 'A'), coefficient(1, 'B')
        with patch.object(active, 'bindings', return_value=[{'employee_id': 1, 'subdivision': 'A'}]), patch.object(active, 'mapping', return_value={}):
            self.assertEqual(active.coefficient_rows([current, removed, foreign]), [current])

    def test_roster_is_parameterized_and_empty_roster_is_not_all(self):
        name = "O'Brien =1"
        with patch.object(active, 'bindings', return_value=[{'employee_id': 1, 'subdivision': 'A', 'full_name': name}]), patch.object(active, 'mapping', return_value={}):
            roster = active.report_roster()
        self.assertEqual(json.loads(roster), [{'subdivision': 'A', 'author': name}])
        with patch('admin_motivation.services.subdivision_aliases.mapping', return_value={}):
            sql, args = bind_sql('SELECT * FROM result WHERE ' + active.REPORT_CONDITION, {'PortalDirectorRoster': roster})
        self.assertNotIn(name, sql)
        self.assertEqual(args, [roster])
        self.assertIn('OPENJSON(?)', sql)
        with patch.object(active, 'bindings', return_value=[]), patch.object(active, 'mapping', return_value={}):
            self.assertEqual(active.report_roster(), '[]')
