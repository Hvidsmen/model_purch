import sqlite3
from django.test import SimpleTestCase
from .services.calculation_scope import predicate


class CalculationScopeTests(SimpleTestCase):
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
