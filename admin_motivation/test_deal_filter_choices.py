from datetime import date
from unittest.mock import Mock, patch
from django.test import TestCase
from django.http import QueryDict
from .services.reports import parameters
from .services.deal_filter_choices import choices


class DealFilterChoicesTests(TestCase):
    def test_each_facet_uses_dates_and_other_filters_with_bound_values(self):
        connection, cursor = Mock(), Mock()
        cursor.description = [('value',)]
        cursor.fetchmany.return_value = [('Option',)]
        values = parameters('deals', QueryDict('type=Политики&subdivision=A&channel=Retail&author=Alex&planning=RAC&goods=Split&brand=Brand&partner=Partner&start=2026-01-01&end=2026-11-03'))
        with patch('admin_motivation.services.deal_filter_choices.connect_database', return_value=(connection, cursor)):
            filters = choices('deals', values)
        queries = cursor.execute.call_args_list
        self.assertEqual(len(queries), 8)
        author_sql, *author_args = queries[3].args
        self.assertIn('[ДатаЗакрытияСделки] BETWEEN ? AND ?', author_sql)
        self.assertIn(date(2026, 1, 1), author_args)
        self.assertIn('Retail', author_args)
        self.assertIn('RAC', author_args)
        self.assertNotIn('Alex', author_args)
        brand_sql, *brand_args = queries[6].args
        self.assertIn('Alex', brand_args)
        self.assertNotIn('Brand', brand_args)
        self.assertNotIn('Alex', brand_sql)
        self.assertEqual(len(filters), 8)
        connection.close.assert_called_once()

    def test_personal_options_never_remove_author_or_subdivision_restrictions(self):
        connection, cursor = Mock(), Mock()
        cursor.description = [('value',)]
        cursor.fetchmany.return_value = [('Option',)]
        values = parameters('deals', QueryDict('type=Директора&subdivision=Forbidden&author=Other&start=2026-01-01&end=2026-11-03'))
        scope = {'type': 'Политики', 'subdivision': 'A', 'author': 'Trusted employee'}
        with patch('admin_motivation.services.deal_filter_choices.connect_database', return_value=(connection, cursor)):
            filters = choices('deals', values, access_scope=scope)
        for call in cursor.execute.call_args_list:
            self.assertIn('Trusted employee', call.args)
            self.assertIn('A', call.args)
            self.assertIn('Политики', call.args)
            self.assertNotIn('Other', call.args)
            self.assertNotIn('Forbidden', call.args)
        author = next(item for item in filters if item['name'] == 'author')
        self.assertTrue(author['locked'])
        self.assertEqual(author['locked_value'], 'Trusted employee')
