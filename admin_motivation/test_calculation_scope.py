import sqlite3
from django.test import SimpleTestCase
from .services.calculation_scope import predicate


class CalculationScopeTests(SimpleTestCase):
    def test_report_defaults_and_rejects_other_subdivisions(self):
        from unittest.mock import patch
        from django.http import QueryDict
        from django.core.exceptions import PermissionDenied, ValidationError
        from .services.calculation_scope import restrict_report
        with patch('admin_motivation.services.subdivision_aliases.mapping', return_value={'Термотрейд': 'Д-Москва'}):
            for value in ('', 'subdivision=Д-Москва', 'subdivision=Термотрейд'):
                query = QueryDict(value, mutable=True)
                restrict_report(query, None)
                self.assertEqual(query.getlist('subdivision'), ['Д-Москва'])
            with self.assertRaises(ValidationError):
                restrict_report(QueryDict('subdivision=Д-Москва&subdivision=Д-Питер', mutable=True), None)
            with self.assertRaises(PermissionDenied):
                restrict_report(QueryDict('', mutable=True), {'subdivision': ['Д-Питер']})

    def test_scope_uses_moscow_after_subdivision_merges(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE subdivisions (SubdivisionKey, SubdivisionName)')
            db.execute('CREATE TABLE merges (SourceName, TargetName)')
            db.executemany('INSERT INTO subdivisions VALUES (?,?)', [
                (1, 'Д-Москва'), (2, 'Д-СПб'), (3, 'REtail'),
                (4, 'Old Moscow'), (5, 'Moved elsewhere'), (6, 'ДПП')])
            db.executemany('INSERT INTO merges VALUES (?,?)', [
                ('Old Moscow', 'Д-Москва'), ('Moved elsewhere', 'Д-СПб')])
            query = ('SELECT s.SubdivisionKey FROM subdivisions s LEFT JOIN merges m ON m.SourceName=s.SubdivisionName '
                     'WHERE ' + predicate('COALESCE(m.TargetName,s.SubdivisionName)'))
            result = db.execute(query.replace("N'", "'")).fetchall()
        self.assertEqual(result, [(1,), (4,)])

