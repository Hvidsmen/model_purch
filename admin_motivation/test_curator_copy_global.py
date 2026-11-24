from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from base.testing import authorize_admin
from .models import CuratorEmployee,CuratorCoefficient,Chanel,Subdivision,Goods,GlobalCoeffVersion,SegmentCoeff,VariationCalculate,GlobalCoeff
from .services import curators


class CuratorGlobalCopyTests(TestCase):
    def setUp(self):
        self.user=authorize_admin(self.client)
        self.version=GlobalCoeffVersion.objects.create(effective_from=date(2026,1,1),title='Копирование')
        self.good=Goods.objects.create(goods_key='COPY-CUR',planning_group_sales='RAC',group='Split',brand='AX')
        self.formula,_=VariationCalculate.objects.get_or_create(variation_name='Подразделение(Глобально)')
        for i in range(5):SegmentCoeff.objects.get_or_create(segment_name='K'+str(i))
        curators.save_global_rates(self.version.pk,self.good.pk,dict.fromkeys(curators.KEYS,'2.5'),self.formula.pk,self.version.revision)
        ch=Chanel.objects.create(chanel_name='Copy')
        self.a=Subdivision.objects.create(subdivision_key='A',subdivision_name='A',subdivision_global='A',chanel=ch)
        self.b=Subdivision.all_objects.create(subdivision_key='B',subdivision_name='B',subdivision_global='B',chanel=ch,merged_into=self.a)
        self.employee=CuratorEmployee.objects.create(full_name='Куратор Копирование')
        self.employee.assigned_subdivisions().add(self.a,self.b)

    def copy(self,targets=None,**kwargs):
        self.version.refresh_from_db()
        return curators.copy_global_rates(self.version.pk,targets or [(self.employee,self.a)],self.version.revision,**kwargs)

    def test_copy_matrix_and_formula_to_selected_physical_pairs(self):
        result=self.copy(targets=[(self.employee,self.a),(self.employee,self.b)])
        self.assertEqual(result['created'],2)
        for row in CuratorCoefficient.objects.all():
            self.assertEqual(row.k0,Decimal('.025'))
            self.assertEqual(row.variation_calculate,self.formula)
        self.assertEqual(CuratorCoefficient.objects.count(),2)

    def test_existing_rates_preserved_unless_replace_selected(self):
        self.copy();row=CuratorCoefficient.objects.get();row.k0=Decimal('.07');row.save()
        self.assertEqual(self.copy()['skipped'],1)
        row.refresh_from_db();self.assertEqual(row.k0,Decimal('.07'))
        self.assertEqual(self.copy(replace=True)['updated'],1)
        row.refresh_from_db();self.assertEqual(row.k0,Decimal('.025'))
        self.assertFalse(CuratorCoefficient.objects.filter(subdivision=self.b).exists())

    def test_incomplete_source_or_stale_revision_never_partially_copies(self):
        self.version.refresh_from_db();old=self.version.revision
        GlobalCoeff.objects.filter(version=self.version,segment__segment_name='K4').first().delete()
        with self.assertRaises(ValidationError):curators.copy_global_rates(self.version.pk,[(self.employee,self.a)],old)
        with self.assertRaises(ValidationError):self.copy()
        self.assertFalse(CuratorCoefficient.objects.exists())

    def test_archived_or_unassigned_recipient_rejected(self):
        other=CuratorEmployee.objects.create(full_name='Не закреплён')
        with self.assertRaises(ValidationError):self.copy(targets=[(other,self.a)])
        self.employee.is_archived=True;self.employee.save()
        with self.assertRaises(ValidationError):self.copy()

    def test_both_pages_offer_copy_and_post_returns_to_curator(self):
        self.version.refresh_from_db()
        global_page=self.client.get(reverse('global_coeff_admin_motivation'),{'version':self.version.pk})
        self.assertContains(global_page,'Скопировать куратору')
        personal=self.client.get(reverse('motivation_curator_rates',args=[self.employee.pk]),{'version':self.version.pk,'subdivision':self.b.pk})
        self.assertContains(personal,'Скопировать из глобальных')
        response=self.client.post(reverse('motivation_curator_copy_global')+'?version='+str(self.version.pk),{'revision':self.version.revision,'targets':f'{self.employee.pk}:{self.b.pk}','return_to':'curator'})
        self.assertEqual(response.status_code,302)
        self.assertIn(f'subdivision={self.b.pk}',response.url)
        self.assertTrue(CuratorCoefficient.objects.filter(subdivision=self.b,k4=Decimal('.025')).exists())

    def test_readonly_and_approved_copy_rejected(self):
        from django.db import models
        self.version.refresh_from_db()
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=self.version.pk),status='approved')
        with self.assertRaises(ValidationError):self.copy()
        self.client.logout()
        self.assertIn(self.client.post(reverse('motivation_curator_copy_global')).status_code,(302,403))
