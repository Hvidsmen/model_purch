from datetime import date
from django.test import TestCase, RequestFactory
from django.contrib.sessions.backends.db import SessionStore
from .models import ScenarioModel
from .services.scenarios import current_scenario, SESSION_KEY


class ScenarioSessionTests(TestCase):
    def setUp(self):
        self.scenario = ScenarioModel.objects.create(name='Session', date_start_plan=date(2026, 1, 1), date_end_plan=date(2026, 12, 31))

    def test_repeated_get_does_not_mark_session_for_write(self):
        request = RequestFactory().get('/model_purch/scenarios/')
        request.session = SessionStore()
        current_scenario(request)
        self.assertTrue(request.session.modified)
        request.session.modified = False
        selected, _ = current_scenario(request)
        self.assertEqual(selected.pk, self.scenario.pk)
        self.assertFalse(request.session.modified)

    def test_switching_scenario_still_marks_session_for_write(self):
        request = RequestFactory().get('/model_purch/scenarios/', {'scenario': self.scenario.pk})
        request.session = SessionStore()
        request.session[SESSION_KEY] = -1
        request.session.modified = False
        current_scenario(request)
        self.assertEqual(request.session[SESSION_KEY], self.scenario.pk)
        self.assertTrue(request.session.modified)
