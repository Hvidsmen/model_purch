from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from . import test_shared_versions
from .models import (DirectorEmployee, GlobalDirectorCoeff, SubdivisionDirectorCoeff,
                     VariationCalculate, GlobalCoeffVersion, SubdivisionCoeff)
from .services.bulk_formula import apply_formula
from .services.versions import create_version
from .services.directors import copy_to_employee
from .services.approval import approve_review, review_approval
from .services.sql_export import export_motivation


class DirectorFormulaTests(TestCase):
    def setUp(self):
        test_shared_versions.SharedMotivationVersionTests.setUp(self)
        self.director = DirectorEmployee.objects.create(full_name='First director')
        self.other_director = DirectorEmployee.objects.create(full_name='Second director')
        self.director.subdivisions.set([self.sub,self.other])
        self.other_director.subdivisions.add(self.sub)
        self.global_ = GlobalDirectorCoeff.objects.create(version=self.first,goods=self.good,k0='.01',k4='.05')
        self.local = SubdivisionDirectorCoeff.objects.create(version=self.first,subdivision=self.sub,employee=self.director,goods=self.good,k0='.02',k4='.06')
        self.another = SubdivisionDirectorCoeff.objects.create(version=self.first,subdivision=self.sub,employee=self.other_director,goods=self.good,k0='.03')
        self.branch = SubdivisionDirectorCoeff.objects.create(version=self.first,subdivision=self.other,employee=self.director,goods=self.good,k0='.04')
        self.formula = VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')[0]
        self.first.refresh_from_db()

    def apply(self, **kwargs):
        self.first.refresh_from_db()
        return apply_formula(self.first.pk,self.first.revision,[self.good.pk],self.formula.pk,['Директора'],**kwargs)

    def test_local_formula_changes_selected_director_only_and_preserves_rates(self):
        self.apply(subdivision_id=self.sub.pk,employee_id=self.director.pk)
        self.local.refresh_from_db()
        self.assertEqual(self.local.variation_calculate,self.formula)
        self.assertEqual(self.local.k0,Decimal('.02'))
        self.assertEqual(self.local.k4,Decimal('.06'))
        for row in (self.global_,self.another,self.branch):
            row.refresh_from_db()
            self.assertIsNone(row.variation_calculate_id)
        self.local_coeff.refresh_from_db()
        self.assertEqual(self.local_coeff.variation_calculate,self.variation)

    def test_global_formula_does_not_change_local_or_core(self):
        self.apply()
        self.global_.refresh_from_db()
        self.local.refresh_from_db()
        self.assertEqual(self.global_.variation_calculate,self.formula)
        self.assertIsNone(self.local.variation_calculate_id)
        self.assertEqual(self.global_.k4,Decimal('.05'))

    def test_missing_director_rates_or_wrong_employee_rejects_without_partial_updates(self):
        self.local.delete()
        self.first.refresh_from_db()
        revision = self.first.revision
        with self.assertRaises(ValidationError):
            apply_formula(self.first.pk,revision,[self.good.pk],self.formula.pk,['Политики','Директора'],self.sub.pk,self.director.pk)
        self.local_coeff.refresh_from_db()
        self.assertEqual(self.local_coeff.variation_calculate,self.variation)
        self.first.refresh_from_db()
        self.assertEqual(self.first.revision,revision)
        with self.assertRaises(ValidationError):
            self.apply(subdivision_id=self.other.pk,employee_id=self.other_director.pk)
        self.assertFalse(SubdivisionDirectorCoeff.objects.filter(pk=self.local.pk).exists())

    def test_version_and_employee_copies_keep_director_formula(self):
        self.apply(subdivision_id=self.sub.pk,employee_id=self.director.pk)
        self.apply()
        approve_review(review_approval(GlobalCoeffVersion.objects.get(pk=self.first.pk))['token'])
        draft = create_version(date(2026,1,1),source_version=self.first.pk)
        self.assertEqual(GlobalDirectorCoeff.objects.get(version=draft).variation_calculate,self.formula)
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(version=draft,subdivision=self.sub,employee=self.director).variation_calculate,self.formula)
        copy_to_employee(draft.pk,self.sub,self.director,self.other_director)
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(version=draft,subdivision=self.sub,employee=self.other_director).variation_calculate,self.formula)

    @patch('admin_motivation.services.calculation_jobs.prepare')
    def test_export_uses_explicit_formula_and_legacy_defaults_for_others(self, prepare):
        self.apply(subdivision_id=self.sub.pk,employee_id=self.director.pk)
        approve_review(review_approval(GlobalCoeffVersion.objects.get(pk=self.first.pk))['token'])
        connection,cursor = Mock(),Mock()
        export_motivation(lambda *args:(connection,cursor))
        rows = [row for call in cursor.executemany.call_args_list if 'SubdivisionMotiveCoeff' in call.args[0] for row in call.args[1] if row[3]=='Директора']
        first = [row for row in rows if row[7]==self.director.employee_code and row[1]==self.sub.subdivision_key]
        self.assertEqual(len(first),5)
        self.assertTrue(all(row[5]==self.formula.variation_name for row in first))
        self.assertTrue(all(row[5]=='Подразделение' for row in rows if row[7]==self.other_director.employee_code))
        prepare.assert_called_once_with(cursor)

    def test_post_preserves_selected_director_in_redirect(self):
        response=self.client.post(reverse('motivation_bulk_formula'),{'version':self.first.pk,'revision':self.first.revision,
            'goods':[self.good.pk],'variation':self.formula.pk,'kinds':['Директора'],'subdivision':self.sub.pk,'employee':self.director.pk})
        self.assertEqual(response.status_code,302)
        self.assertIn(f'employee={self.director.pk}',response.url)
        self.local.refresh_from_db()
        self.assertEqual(self.local.variation_calculate,self.formula)
