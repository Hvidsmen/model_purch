from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.test import TestCase
from .models import MotivationSubdivisionExclusion
from .services.subdivision_scope import exclusion_scope, included
from .services.sales_plans import source_sql


class DppExclusionSeedTests(TestCase):
    def repair(self):
        import_module('admin_motivation.migrations.0044_exclude_dpp_subdivision').exclude_dpp(
            apps, SimpleNamespace(connection=connection))

    def test_initial_seed_and_installed_schema_both_include_dpp(self):
        self.assertTrue(MotivationSubdivisionExclusion.objects.get(subdivision_name='ДПП').enabled)
        MotivationSubdivisionExclusion.objects.all().delete()
        import_module('admin_motivation.migrations.0043_motivationsubdivisionexclusion').seed(
            apps, SimpleNamespace(connection=connection))
        self.assertTrue(MotivationSubdivisionExclusion.objects.get(subdivision_name='ДПП').enabled)
        with exclusion_scope():
            self.assertFalse(included(' ДПП '))
            self.assertFalse(included('дпп'))
            for source in ('planning','erp'):
                sql=source_sql(source)
                self.assertIn("UPPER(N'ДПП')",sql)
                self.assertNotIn('Chanel NOT IN',sql)

    def test_repair_adds_missing_name_and_is_idempotent(self):
        MotivationSubdivisionExclusion.objects.filter(subdivision_name='ДПП').delete()
        self.repair();self.repair()
        self.assertEqual(MotivationSubdivisionExclusion.objects.filter(subdivision_name='ДПП',enabled=True).count(),1)

    def test_repair_enables_existing_variant_and_preserves_other_settings(self):
        MotivationSubdivisionExclusion.objects.filter(subdivision_name='ДПП').delete()
        row=MotivationSubdivisionExclusion.objects.create(subdivision_name=' дпп ',enabled=False,comment='Keep comment')
        other=MotivationSubdivisionExclusion.objects.create(subdivision_name='Other',enabled=False)
        self.repair();self.repair()
        row.refresh_from_db();other.refresh_from_db()
        self.assertTrue(row.enabled);self.assertEqual(row.comment,'Keep comment')
        self.assertFalse(other.enabled)
        self.assertFalse(MotivationSubdivisionExclusion.objects.filter(subdivision_name='ДПП').exists())
        with exclusion_scope():self.assertFalse(included('ДПП'))

    def test_previously_loaded_dpp_is_filtered_before_scenario_coverage(self):
        from . import test_sales_plans as fixtures
        from .services.sales_plans import load_plan
        from .services.coefficient_iteration_calculation import plan_rows
        fixture=fixtures.SalesPlanTests();fixture.setUp()
        fixture.cursor.description.append(('SubdivisionKey',))
        fixture.cursor.fetchall.return_value=[(*row,key) for row,key in zip(fixture.rows(),('KEY-A','KEY-B'))]
        load_plan(fixture.scenario.pk,fixture.connector)
        line=fixture.scenario.lines.get(subdivision='A')
        line.pk=None;line.subdivision='ДПП';line.source_subdivisions={'DPP-ID':next(iter(line.source_subdivisions.values()))}
        line.save()
        fixture.scenario.refresh_from_db()
        with exclusion_scope():
            rows=plan_rows(fixture.scenario,2026)
        self.assertEqual({row['branch'] for row in rows},{'KEY-A','KEY-B'})
        self.assertTrue(fixture.scenario.lines.filter(subdivision='ДПП').exists())
