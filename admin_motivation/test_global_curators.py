from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase,SimpleTestCase
from django.urls import reverse
from base.testing import authorize_admin
from .models import GlobalCoeffVersion,Goods,SegmentCoeff,VariationCalculate,GlobalCoeff,TypeCoeff
from .services import curators
from .services.comparison import Index


class GlobalCuratorTests(TestCase):
    def setUp(self):
        self.user=authorize_admin(self.client)
        self.version=GlobalCoeffVersion.objects.create(effective_from=date(2026,1,1),title='Глобальные кураторы')
        self.good=Goods.objects.create(goods_key='CUR-GLOBAL',planning_group_sales='1. RAC',group='Split',brand='AX')
        for i in range(5):SegmentCoeff.objects.get_or_create(segment_name='K'+str(i))
        self.formula,_=VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')

    def save_rates(self):
        self.version.refresh_from_db()
        curators.save_global_rates(self.version.pk,self.good.pk,dict.fromkeys(curators.KEYS,'2.5'),self.formula.pk,self.version.revision)

    def test_saved_matrix_is_versioned_visible_and_cloneable(self):
        self.save_rates()
        self.assertEqual(GlobalCoeff.objects.filter(version=self.version,type_coeff__type_coeff_name='Кураторы').count(),5)
        context=curators.global_context(self.version,self.good.pk)
        self.assertEqual(context['curator_inputs'][0]['value'],Decimal('2.500'))
        from .services.versions import create_version
        copied=create_version(date(2026,2,1),source_version=self.version.pk)
        self.assertEqual(GlobalCoeff.objects.filter(version=copied,type_coeff__type_coeff_name='Кураторы').count(),5)

    def test_stale_and_invalid_matrix_cannot_partially_save(self):
        for values in (dict.fromkeys(curators.KEYS,'NaN'),dict.fromkeys(curators.KEYS,'101')):
            with self.assertRaises(ValidationError):curators.save_global_rates(self.version.pk,self.good.pk,values,self.formula.pk,self.version.revision)
        self.assertFalse(GlobalCoeff.objects.exists())
        self.save_rates()
        with self.assertRaises(ValidationError):curators.save_global_rates(self.version.pk,self.good.pk,dict.fromkeys(curators.KEYS,'3'),self.formula.pk,self.version.revision)

    def test_global_page_has_curator_block_and_edit_form(self):
        self.save_rates()
        response=self.client.get(reverse('global_coeff_admin_motivation'),{'version':self.version.pk})
        self.assertContains(response,'data-coefficient-group-toggle="curators"')
        self.assertContains(response,'class="header-curators"')
        self.assertContains(response,reverse('motivation_curator_global_rates'))
        self.assertContains(response,'CUR-GLOBAL')

    def test_curator_cells_are_in_main_matrix_and_save_with_the_shared_table(self):
        self.save_rates()
        from .services.coefficient_groups import global_matrix
        from .services.save_verification import SaveVerification
        from .services.editor_patch import save_table
        matrix = global_matrix(self.version)
        good = next(iter(matrix['1. RAC']['Split']))
        self.assertEqual(len(good.curator_cells), 5)
        self.assertTrue(all(cell.type_coeff.type_coeff_name == 'Кураторы' for cell in good.curator_cells))
        self.version.refresh_from_db()
        cell = good.curator_cells[0]
        data = {'revision':str(self.version.revision), f'global_coeff={cell.pk}':'3%'}
        verification = SaveVerification(self.version, data)
        save_table(self.version, data, verification)
        cell.refresh_from_db()
        self.assertEqual(Decimal(str(cell.motivation_coeff)), Decimal('.03'))
        self.assertEqual(GlobalCoeff.objects.filter(version=self.version,type_coeff__type_coeff_name='Кураторы').count(), 5)

    def test_approved_rates_cannot_be_changed(self):
        self.save_rates()
        from django.db import models
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=self.version.pk),status='approved')
        with self.assertRaises(ValidationError):self.save_rates()

    def test_global_and_personal_curator_bases_are_not_silently_identical(self):
        self.save_rates()
        from .services.comparison import coefficient_rows
        index=Index(coefficient_rows([self.version.pk]))
        matrix,_=index.coefficients(self.version.pk,'','1. RAC','Split','AX',kinds=['Кураторы'])
        self.assertEqual(sum(rate*100 for rate in matrix[0]),Decimal('12.500'))
