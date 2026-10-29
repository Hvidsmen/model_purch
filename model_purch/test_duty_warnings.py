from types import SimpleNamespace
from django.test import SimpleTestCase
from .services.plan_validation import duty_warnings
from .goods_identity import planning_group_key


class DutyWarningsTests(SimpleTestCase):
    def check_rate(self, rate, group):
        return [kind for kind, _ in duty_warnings(SimpleNamespace(duty_rate=rate, group_goods=' RAC '), {planning_group_key('rac'): group})]

    def test_percentage_and_group_checks(self):
        self.assertEqual(self.check_rate(.1, .1), [])
        self.assertEqual(self.check_rate(.001, .1), ['duty_small', 'duty_group'])
        self.assertEqual(self.check_rate(0, 0), [])
        self.assertEqual(self.check_rate(0, .1), ['duty_group'])
        self.assertEqual(self.check_rate(.01, .01), [])

    def test_unknown_group_does_not_imply_mismatch(self):
        self.assertEqual(duty_warnings(SimpleNamespace(duty_rate=.1, group_goods='Unknown'), {}), [])
