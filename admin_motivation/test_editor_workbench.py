import json
from django.test import TestCase
from django.urls import reverse
from . import test_shared_versions
from .models import CoefficientEditLog, VariationCalculate


class EditorWorkbenchTests(TestCase):
    setUp = test_shared_versions.SharedMotivationVersionTests.setUp

    def fields(self, **extra):
        self.first.refresh_from_db()
        return {'version': self.first.pk, 'revision': self.first.revision, **extra}

    def preflight(self, fields, **extra):
        return self.client.post(reverse('motivation_editor_check'), json.dumps({'fields': fields, **extra}), content_type='application/json')

    def test_preflight_pending_formula_and_rate_never_write(self):
        formula=VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')[0]
        fields=self.fields(**{f'global_coeff={self.global_coeff.pk}':'20%', f'formula_coeff={self.global_coeff.pk}':formula.pk})
        response=self.preflight(fields)
        self.assertEqual(response.status_code,200,response.content)
        self.assertGreater(response.json()['changed'],0)
        self.global_coeff.refresh_from_db();self.first.refresh_from_db()
        self.assertEqual(self.global_coeff.motivation_coeff,.1)
        self.assertEqual(self.global_coeff.variation_calculate_id,self.variation.pk)
        self.assertEqual(self.first.revision,fields['revision'])
        self.assertFalse(CoefficientEditLog.objects.exists())

    def test_save_formula_and_rate_in_one_verified_commit(self):
        formula=VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')[0]
        fields=self.fields(**{f'global_coeff={self.global_coeff.pk}':'20%', f'formula_coeff={self.global_coeff.pk}':formula.pk})
        self.client.post(reverse('gb_act'),{**fields,'action_button':'save_coeff'})
        self.global_coeff.refresh_from_db()
        self.assertEqual(self.global_coeff.motivation_coeff,.2)
        self.assertEqual(self.global_coeff.variation_calculate_id,formula.pk)
        log=CoefficientEditLog.objects.get()
        self.assertEqual({item['field'] for item in log.changes},{'motivation_coeff','variation_calculate_id'})

    def test_foreign_formula_or_stale_revision_reject_entire_patch(self):
        fields=self.fields(**{f'global_coeff={self.global_coeff.pk}':'20%', f'formula_coeff={self.local_coeff.pk+99999}':self.variation.pk})
        self.assertEqual(self.preflight(fields).status_code,400)
        self.client.post(reverse('gb_act'),{**fields,'action_button':'save_coeff'})
        self.global_coeff.refresh_from_db()
        self.assertEqual(self.global_coeff.motivation_coeff,.1)
        self.assertFalse(CoefficientEditLog.objects.exists())
        fields=self.fields();fields['revision']=-1
        self.assertEqual(self.preflight(fields).status_code,400)

    def test_manager_and_local_rate_save_together(self):
        fields=self.fields(**{f'sub_coeff={self.local_coeff.pk}':'25%',f'kind_coeff={self.manager_kind.pk}':'90%'})
        self.client.post(reverse('sub_act',args=[self.sub.pk]),{**fields,'action_button':'save_coeff'})
        self.local_coeff.refresh_from_db();self.manager.refresh_from_db()
        self.assertEqual(self.local_coeff.motivation_coeff,.25)
        self.assertEqual(self.manager.coeff,.9)
        self.assertEqual(CoefficientEditLog.objects.count(),1)

    def test_preflight_rejects_unknown_fields_and_invalid_rates(self):
        for extra in ({'unexpected':'x'},{f'global_coeff={self.global_coeff.pk}':'200%'}):
            response=self.preflight(self.fields(**extra))
            self.assertEqual(response.status_code,400,response.content)
        self.assertFalse(CoefficientEditLog.objects.exists())

    def test_copy_is_audited_and_failed_confirmation_is_not(self):
        from datetime import date
        from .services.versions import create_version
        draft=create_version(date(2027,1,1),'Audit copy',self.first.pk)
        draft.coefficients.update(motivation_coeff=.9)
        data={'action_button':'copy_version','version':draft.pk,'copy_source':self.first.pk}
        self.client.post(reverse('gb_act'),data)
        self.assertFalse(CoefficientEditLog.objects.exists())
        self.client.post(reverse('gb_act'),{**data,'confirm_copy':'yes'})
        log=CoefficientEditLog.objects.get()
        self.assertEqual(log.version_id,draft.pk)
        self.assertIn('Копирование',log.scope_label)
        self.assertEqual(log.changes[0]['before'],'0.9')
        self.assertEqual(log.changes[0]['after'],'0.1')
        self.client.post(reverse('gb_act'),{**data,'confirm_copy':'yes'})
        self.assertEqual(CoefficientEditLog.objects.count(),1)


class EditorPlanPreflightTests(TestCase):
    def setUp(self):
        from . import test_formula_defaults
        test_formula_defaults.FormulaDefaultTests.setUp(self)

    def test_overlay_checks_loaded_plan_without_saving_or_changing_source(self):
        row=self.version.subdivision_coefficients.first()
        row.refresh_from_db();self.version.refresh_from_db()
        formula=VariationCalculate.objects.get_or_create(variation_name='Подразделение')[0]
        fields={'version':str(self.version.pk),'revision':str(self.version.revision),f'formula_coeff={row.pk}':str(formula.pk)}
        payload={'fields':fields,'subdivision':row.subdivision_id,'plan':self.fixture.scenario.pk}
        response=self.client.post(reverse('motivation_editor_check'),json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertGreater(response.json()['coverage']['examined'],0)
        row.refresh_from_db();self.version.refresh_from_db()
        self.assertNotEqual(row.variation_calculate_id,formula.pk)
        self.assertEqual(str(self.version.revision),fields['revision'])
        self.assertFalse(CoefficientEditLog.objects.exists())
        payload['fields']={'version':str(self.version.pk),'revision':str(self.version.revision),f'delete_coeff={row.goods_id}':'on'}
        response=self.client.post(reverse('motivation_editor_check'),json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertGreater(response.json()['coverage']['missing'],0)
        self.assertTrue(self.version.subdivision_coefficients.filter(pk=row.pk).exists())
