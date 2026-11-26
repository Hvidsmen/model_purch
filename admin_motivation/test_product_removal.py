from datetime import date
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase
from django.urls import reverse
from .test_planning_group_rename import PlanningRenameTests
from .models import (Goods,Subdivision,Chanel,DirectorEmployee,CuratorEmployee,SubdivisionCoeff,
                     SubdivisionDirectorCoeff,CuratorCoefficient)
from .services import product_removal as service
from .services.versions import create_version


class ProductRemovalTests(TestCase):
    setUp=PlanningRenameTests.setUp
    seed=PlanningRenameTests.seed

    def setup_rates(self):
        good,row,director=self.seed()
        branch=Subdivision.objects.create(subdivision_key='Test',subdivision_name='Test',subdivision_global='Test',chanel=Chanel.objects.create(chanel_name='Test'))
        employee=DirectorEmployee.objects.create(full_name='Director')
        curator=CuratorEmployee.objects.create(full_name='Curator')
        SubdivisionCoeff.objects.create(version=self.version,goods=good,subdivision=branch,type_coeff=row.type_coeff,segment=row.segment,motivation_coeff=.03,manager_coeff=.9,variation_calculate=row.variation_calculate)
        SubdivisionDirectorCoeff.objects.create(version=self.version,goods=good,subdivision=branch,employee=employee,k1=.02)
        CuratorCoefficient.objects.create(version=self.version,goods=good,subdivision=branch,employee=curator,k2=.04)
        self.version.refresh_from_db()
        return good

    def test_global_only_then_explicit_orphan_cleanup(self):
        good=self.setup_rates()
        self.assertFalse(service.available(self.version,True).filter(pk=good.pk).exists())
        service.apply(self.actor,self.version.pk,self.version.revision,[good.pk],False)
        self.version.refresh_from_db()
        self.assertEqual(service.preview(self.version,[good.pk],True)['total'],3)
        self.assertTrue(service.available(self.version,True).filter(pk=good.pk).exists())
        with self.assertRaises(ValidationError):service.preview(self.version,[good.pk],False)
        plan=service.apply(self.actor,self.version.pk,self.version.revision,[good.pk],True)
        self.assertEqual(plan['total'],3)
        for model in service.MODELS:self.assertFalse(model.objects.filter(version=self.version,goods=good).exists())
        self.assertTrue(Goods.objects.filter(pk=good.pk).exists())

    def test_cascade_preserves_history_other_goods_and_group_rules(self):
        good=self.setup_rates()
        newer=create_version(date(2027,1,1),source_version=self.version.pk);newer.refresh_from_db()
        other=Goods.objects.create(goods_key='other',planning_group_sales='4.VENT',group='other',brand='*')
        from .models import GlobalDirectorCoeff
        GlobalDirectorCoeff.objects.create(version=newer,goods=other,k0=.01)
        newer.refresh_from_db()
        plan=service.preview(newer,[good.pk],True)
        self.assertEqual(plan['total'],5)
        self.assertTrue(plan['scopes'])
        service.apply(self.actor,newer.pk,newer.revision,[good.pk],True)
        for model in service.MODELS:
            self.assertFalse(model.objects.filter(version=newer,goods=good).exists())
            self.assertTrue(model.objects.filter(version=self.version,goods=good).exists())
        self.assertTrue(GlobalDirectorCoeff.objects.filter(version=newer,goods=other).exists())
        newer.refresh_from_db();self.assertEqual(newer.planning_rules,self.version.planning_rules)
        self.assertEqual(newer.edit_history.get().changed_count,5)
        with self.assertRaises(ValidationError):service.apply(self.actor,self.version.pk,self.version.revision,[good.pk],True)

    def test_preview_confirmation_scope_change_and_stale_revision(self):
        good=self.setup_rates();url=reverse('motivation_product_removal')
        data={'version':self.version.pk,'revision':self.version.revision,'goods':[good.pk],'include_local':'on','action':'apply'}
        self.assertContains(self.client.post(url,data),'Сначала выполните предварительный просмотр.')
        data['action']='preview';response=self.client.post(url,data)
        self.assertEqual(response.context['plan']['total'],5)
        token=response.context['preview_token']
        data.update(action='apply',preview_token=token)
        changed=dict(data);changed.pop('include_local')
        self.assertContains(self.client.post(url,changed),'Параметры или версия изменились.')
        with self.assertRaises(ValidationError):service.apply(self.actor,self.version.pk,self.version.revision-1,[good.pk],True)
        self.assertEqual(self.client.post(url,data).status_code,302)

    def test_unauthorized_and_unknown_goods_rejected(self):
        from django.contrib.auth.models import User
        good=self.setup_rates()
        with self.assertRaises(PermissionDenied):service.apply(User.objects.create_user('denied'),self.version.pk,self.version.revision,[good.pk],True)
        with self.assertRaises(ValidationError):service.apply(self.actor,self.version.pk,self.version.revision,[good.pk,999999],True)
        self.assertEqual(service.preview(self.version,[good.pk],True)['total'],5)
