from copy import deepcopy
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from .test_planning_group_rules import GroupRuleVersionTests,RULE
from .models import Goods,GlobalCoeff,GlobalDirectorCoeff,TypeCoeff,SegmentCoeff,VariationCalculate,GlobalCoeffVersion
from .services import planning_group_rename as service


class PlanningRenameTests(TestCase):
    setUp=GroupRuleVersionTests.setUp

    def seed(self):
        good=Goods.objects.create(planning_group_sales=' 4.VENT',group='Fans',brand='*',goods_key='old-fans')
        kind=TypeCoeff.objects.create(type_coeff_name='Политики')
        segment=SegmentCoeff.objects.create(segment_name='K0')
        formula=VariationCalculate.objects.create(variation_name='Группа планов продаж')
        row=GlobalCoeff.objects.create(version=self.version,goods=good,type_coeff=kind,segment=segment,motivation_coeff=.0123,manager_coeff=1,variation_calculate=formula)
        director=GlobalDirectorCoeff.objects.create(version=self.version,goods=good,k0=Decimal('.004'))
        self.version.refresh_from_db()
        self.version.planning_rules=[{**RULE,'planning':good.planning_group_sales}]
        self.version.save(update_fields=['planning_rules'])
        self.version.refresh_from_db()
        return good,row,director

    def test_preview_then_move_preserves_values_and_historical_product(self):
        good,row,director=self.seed()
        old=deepcopy(self.version.planning_rules)
        # Historical versions share the same Goods: the rename must not edit it.
        before=good.planning_group_sales
        plan=service.preview(self.version,before,' 4. VENT ')
        self.assertEqual(plan['total'],2)
        self.assertFalse(plan['conflicts'])
        self.assertEqual(GlobalCoeff.objects.get(pk=row.pk).goods_id,good.pk)
        service.apply(self.actor,self.version.pk,self.version.revision,before,' 4. VENT ')
        good.refresh_from_db();row.refresh_from_db();director.refresh_from_db();self.version.refresh_from_db()
        self.assertEqual(good.planning_group_sales,before)
        self.assertEqual(row.goods.planning_group_sales,'4. VENT')
        self.assertEqual(row.motivation_coeff,.0123)
        self.assertEqual(director.k0,Decimal('.004'))
        self.assertEqual(row.goods_id,director.goods_id)
        self.assertEqual(self.version.planning_rules[0],{**old[0],'planning':'4. VENT'})
        self.assertEqual(self.version.edit_history.count(),1)

    def test_existing_rates_and_rules_block_all_changes(self):
        good,row,director=self.seed()
        target=Goods.objects.create(planning_group_sales='4. VENT',group=good.group,brand=good.brand,goods_key='target')
        GlobalDirectorCoeff.objects.create(version=self.version,goods=target,k0=.02)
        self.version.refresh_from_db()
        self.assertTrue(service.preview(self.version,good.planning_group_sales,'4. VENT')['conflicts'])
        with self.assertRaises(ValidationError):service.apply(self.actor,self.version.pk,self.version.revision,good.planning_group_sales,'4. VENT')
        row.refresh_from_db();self.assertEqual(row.goods_id,good.pk)
        self.version.planning_rules.append({**self.version.planning_rules[0],'planning':'4. VENT'})
        self.version.save(update_fields=['planning_rules'])
        self.assertGreaterEqual(len(service.preview(self.version,good.planning_group_sales,'4. VENT')['conflicts']),2)

    def test_stale_revision_and_no_preview_rejected(self):
        good,row,director=self.seed()
        with self.assertRaises(ValidationError):service.apply(self.actor,self.version.pk,self.version.revision-1,good.planning_group_sales,'4. VENT')
        url=reverse('motivation_planning_group_rename')
        data={'version':self.version.pk,'revision':self.version.revision,'source':good.planning_group_sales,'target':'4. VENT','action':'apply'}
        response=self.client.post(url,data)
        self.assertContains(response,'Подтвердите замену через предварительный просмотр.')
        data['action']='preview'
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.context['plan']['conflicts'])
        data.update(action='apply',preview_token=response.context['preview_token'])
        self.assertEqual(self.client.post(url,data).status_code,302)
        row.refresh_from_db();self.assertEqual(row.goods.planning_group_sales,'4. VENT')

    def test_all_scopes_move_but_previous_version_stays_unchanged(self):
        from datetime import date
        from .models import (Subdivision,Chanel,DirectorEmployee,CuratorEmployee,
                             SubdivisionCoeff,SubdivisionDirectorCoeff,CuratorCoefficient)
        from .services.versions import create_version
        good,row,director=self.seed()
        branch=Subdivision.objects.create(subdivision_key='Test',subdivision_name='Test',subdivision_global='Test',chanel=Chanel.objects.create(chanel_name='Test'))
        employee=DirectorEmployee.objects.create(full_name='Director')
        curator=CuratorEmployee.objects.create(full_name='Curator')
        local=SubdivisionCoeff.objects.create(version=self.version,goods=good,subdivision=branch,type_coeff=row.type_coeff,segment=row.segment,motivation_coeff=.03,manager_coeff=.9,variation_calculate=row.variation_calculate)
        local_director=SubdivisionDirectorCoeff.objects.create(version=self.version,goods=good,subdivision=branch,employee=employee,k1=.02)
        local_curator=CuratorCoefficient.objects.create(version=self.version,goods=good,subdivision=branch,employee=curator,k2=.04)
        self.version.refresh_from_db()
        newer=create_version(date(2027,1,1),source_version=self.version.pk)
        newer.refresh_from_db()
        snapshots={m:list(m.objects.filter(version=newer).values()) for m in service.MODELS}
        result=service.apply(self.actor,newer.pk,newer.revision,good.planning_group_sales,'4. VENT')
        self.assertEqual(result['total'],5)
        self.assertEqual(result['subdivisions'],['Test'])
        for model,expected in snapshots.items():
            actual=list(model.objects.filter(version=newer).values())
            for old,new in zip(expected,actual):
                self.assertNotEqual(old['goods_id'],new['goods_id'])
                self.assertEqual({k:v for k,v in old.items() if k!='goods_id'},{k:v for k,v in new.items() if k!='goods_id'})
        for old in (row,director,local,local_director,local_curator):
            old.refresh_from_db();self.assertEqual(old.goods_id,good.pk)
        self.version.refresh_from_db()
        self.assertEqual(self.version.planning_rules[0]['planning'],good.planning_group_sales)
        with self.assertRaises(ValidationError):service.apply(self.actor,self.version.pk,self.version.revision,good.planning_group_sales,'4. VENT')

    def test_permissions_key_collision_and_empty_target_reuse(self):
        from django.contrib.auth.models import User
        from django.core.exceptions import PermissionDenied
        good,row,director=self.seed()
        actor=User.objects.create_user('no-access')
        with self.assertRaises(PermissionDenied):service.apply(actor,self.version.pk,self.version.revision,good.planning_group_sales,'4. VENT')
        collision=Goods.objects.create(goods_key='4. VENTFans*',planning_group_sales='Other',group='Other',brand='Other')
        self.assertTrue(service.preview(self.version,good.planning_group_sales,'4. VENT')['conflicts'])
        collision.delete()
        target=Goods.objects.create(goods_key='target-empty',planning_group_sales='4. VENT',group='Fans',brand='*')
        service.apply(self.actor,self.version.pk,self.version.revision,good.planning_group_sales,'4. VENT')
        row.refresh_from_db();self.assertEqual(row.goods_id,target.pk)
