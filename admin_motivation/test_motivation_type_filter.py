from html.parser import HTMLParser
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from base.access import ADMIN, POLITICIAN
from .reports_all_2026 import selection


class MotivationTypeFilterTests(SimpleTestCase):
    def test_checkbox_filter_preserves_multiple_selected_types(self):
        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'input' and attrs.get('name') == 'type':
                    self.values[attrs['value']] = 'checked' in attrs

        html = render_to_string('admin_motivation/reports_next/_motivation_types.html', {
            'kinds': ['Политики', 'Продажи', 'Директора'], 'selected_types': ['Политики', 'Продажи']})
        inputs = Inputs()
        inputs.feed(html)
        self.assertEqual(inputs.values, {'Политики': True, 'Продажи': True, 'Директора': False})
        self.assertIn('Политики, Продажи', html)
        self.assertNotIn('<select', html)

    def test_server_accepts_combination_and_empty_selection_means_all_available(self):
        with patch('admin_motivation.reports_all_2026.politician_scope', return_value=None), patch(
                'admin_motivation.reports_all_2026.role_for', return_value=ADMIN):
            self.assertEqual(selection(object(), QueryDict('type=Политики&type=Продажи'))[1], ['Политики', 'Продажи'])
            self.assertEqual(selection(object(), QueryDict(''))[1], ['Политики', 'Продажи', 'Директора'])

    def test_multiple_selection_cannot_expand_access_scope(self):
        scope = {'subdivision': ['A'], 'type': ['Политики', 'Продажи']}
        with patch('admin_motivation.reports_all_2026.politician_scope', return_value=scope), patch(
                'admin_motivation.reports_all_2026.role_for', return_value=POLITICIAN):
            self.assertEqual(selection(object(), QueryDict('type=Политики&type=Продажи'))[1], ['Политики', 'Продажи'])
            with self.assertRaises(PermissionDenied):
                selection(object(), QueryDict('type=Политики&type=Директора'))
