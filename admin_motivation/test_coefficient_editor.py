from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User, Group
from base.access import FINANCIER, MODELER
from . import test_shared_versions
from .models import CoefficientEditLog, GlobalDirectorCoeff
from .services.save_verification import SaveVerification
from .services.coefficient_groups import coefficient_matrix


class CoefficientEditorTests(TestCase):
    setUp = test_shared_versions.SharedMotivationVersionTests.setUp

    def payload(self, value='20%'):
        self.first.refresh_from_db()
        return {'version':self.first.pk,'revision':self.first.revision,'action_button':'save_coeff',
                f'global_coeff={self.global_coeff.pk}':value,
                f'select_var_calc={self.good.pk}':self.variation.pk,
                f'original_var_calc={self.good.pk}':self.variation.pk}

    def test_verified_save_records_values_actor_and_revision_but_not_noop(self):
        self.client.post(reverse('gb_act'), self.payload())
        log=CoefficientEditLog.objects.get()
        self.assertEqual(log.version_id,self.first.pk)
        self.assertIsNotNone(log.actor_id)
        self.assertGreater(log.revision_after,log.revision_before)
        change=log.changes[0]
        self.assertEqual(change['before'],'0.1')
        self.assertEqual(change['after'],'0.2')
        self.assertEqual(change['goods_id'],self.good.pk)
        self.assertEqual(change['kind'],'Политики')
        self.assertEqual(change['segment'],'Segment')
        self.client.post(reverse('gb_act'), self.payload())
        self.assertEqual(CoefficientEditLog.objects.count(),1)
        with self.assertRaises(ValidationError):
            log.changed_count=99;log.save()

    def test_failed_readback_and_stale_revision_never_create_history(self):
        with patch.object(SaveVerification,'verify',side_effect=ValidationError('Rollback')):
            self.client.post(reverse('gb_act'),self.payload())
        self.global_coeff.refresh_from_db()
        self.assertEqual(self.global_coeff.motivation_coeff,.1)
        self.assertFalse(CoefficientEditLog.objects.exists())
        payload=self.payload();self.global_coeff.motivation_coeff=.3;self.global_coeff.save()
        self.client.post(reverse('gb_act'),payload)
        self.assertFalse(CoefficientEditLog.objects.exists())

    def test_global_reference_is_exact_and_missing_is_not_zero(self):
        matrix=coefficient_matrix(self.first,self.sub)
        good=next(iter(next(iter(next(iter(matrix.values())).values()))))
        self.assertEqual(good.director_cells[0]['exists'],False)
        self.assertIsNone(good.director_cells[0]['global_value'])
        row=next(iter(next(iter(next(iter(matrix.values())).values())).values()))[0][0]
        self.assertEqual(row.editor_global_value,.1)
        self.global_coeff.delete()
        matrix=coefficient_matrix(self.first,self.sub)
        row=next(iter(next(iter(next(iter(matrix.values())).values())).values()))[0][0]
        self.assertIsNone(row.editor_global_value)

    def test_untouched_director_zero_placeholders_do_not_create_exact_rates(self):
        payload=self.payload()
        payload.update({f'director_coeff={self.good.pk}={i}':'0%' for i in range(5)})
        self.client.post(reverse('gb_act'),payload)
        self.assertFalse(GlobalDirectorCoeff.objects.exists())
        self.assertEqual(CoefficientEditLog.objects.get().changed_count,1)

    def test_history_csv_and_modeler_action_obey_existing_roles(self):
        self.good.planning_group_sales='=EXTERNAL()';self.good.save()
        self.client.post(reverse('gb_act'),self.payload())
        log=CoefficientEditLog.objects.get()
        url=reverse('motivation_coefficient_edit_log',args=[log.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertIn("'=EXTERNAL()",response.content.decode('utf-8'))
        for role,expected in ((FINANCIER,403),(MODELER,200)):
            user=User.objects.create_user('editor-'+role);user.groups.add(Group.objects.get(name=role));self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code,expected)
        page=self.client.get(reverse('global_coeff_admin_motivation'))
        self.assertContains(page,'multiply-operation')
        self.assertContains(page,'История сохранений этой таблицы')


    def test_history_keeps_goods_label_after_reference_changes(self):
        self.client.post(reverse('gb_act'),self.payload())
        log=CoefficientEditLog.objects.get()
        self.assertEqual(log.changes[0]['goods_label'],'Sales / Group / Brand')
        self.good.brand='Renamed';self.good.save()
        response=self.client.get(reverse('motivation_coefficient_edit_log',args=[log.pk]))
        self.assertContains(response,'Sales / Group / Brand')
        self.assertNotContains(response,'Renamed')

    def test_bulk_formula_is_audited_only_when_applied(self):
        from .models import VariationCalculate
        variation=VariationCalculate.objects.create(variation_name='Подразделение')
        self.first.refresh_from_db()
        payload={'version':self.first.pk,'revision':self.first.revision,'goods':[self.good.pk],'variation':variation.pk,'kinds':['Политики'],'preview':'1'}
        self.client.post(reverse('motivation_bulk_formula'),payload)
        self.assertFalse(CoefficientEditLog.objects.exists())
        del payload['preview']
        self.client.post(reverse('motivation_bulk_formula'),payload)
        log=CoefficientEditLog.objects.get()
        self.assertEqual(log.changes[0]['field'],'variation_calculate_id')
        self.assertEqual(log.changes[0]['after'],str(variation.pk))
        self.global_coeff.refresh_from_db()
        self.assertEqual(self.global_coeff.motivation_coeff,.1)


    def test_matrix_keeps_policy_sales_columns_when_reference_types_differ(self):
        from .models import TypeCoeff, GlobalCoeff
        extra=TypeCoeff.objects.create(type_coeff_name='Директора')
        GlobalCoeff.objects.create(version=self.first,goods=self.good,type_coeff=extra,segment=self.segment,variation_calculate=self.variation,motivation_coeff=.9,manager_coeff=1)
        matrix=coefficient_matrix(self.first)
        cells=matrix['Sales']['Group'][self.good]
        self.assertEqual(len(cells),2)
        self.assertEqual(cells[0][0].pk,self.global_coeff.pk)
        self.assertIsNone(cells[1][0])
        good=next(iter(matrix['Sales']['Group']))
        self.assertEqual(good.editor_formula_id,self.variation.pk)
