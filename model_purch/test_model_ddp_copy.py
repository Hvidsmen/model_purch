from copy import deepcopy
from datetime import date
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, RequestFactory
from django.urls import reverse
from base.testing import authorize_admin
from base.models import UserProjectAccess
from base.access import ANALYST
from .models import ModelDDPCalculation,ModelDDPRow,GoodsGroup
from .services.model_ddp_copy import preview,apply
from .test_model_ddp import PARAMS,RATES


class CopyTests(TestCase):
    def setUp(self):
        self.user=authorize_admin(self.client)
        GoodsGroup.objects.create(name='Goods',container_volume=50,duty_rate=.1)
        self.source=ModelDDPCalculation.objects.create(name='Source',as_of=date(2026,10,1),created_by=self.user,
            parameters={**PARAMS,'price_per_container':'2000'},rates={**RATES,'USD':'120'})
        self.target=ModelDDPCalculation.objects.create(name='Target',as_of=date(2026,11,22),created_by=self.user,parameters=PARAMS,rates=RATES)
        self.factory=RequestFactory()
        self.rows(self.source,3,source=True)
        self.rows(self.target,3)
    def rows(self,batch,count,source=False):
        ModelDDPRow.objects.bulk_create([ModelDDPRow(calculation=batch,model_ref=str(i),model_name='Model '+str(i),
            price_type_ref='k',price_type='PURCH USD',currency='USD',price=200 if source else 100,
            source_data={'volume':'1','classification':{'brand':'A' if i%2==0 else 'B','group_goods':'Goods','supply_type':'p2'}},
            overrides={'volume':'2'} if source else {}) for i in range(count)])
    def make_preview(self,mode='rows',**filters):
        request=self.factory.get('/',filters);request.user=self.user
        return preview(self.target,self.source,mode,request)

    def test_filtered_rows_preserve_prices_dates_globals_and_other_rows(self):
        original=deepcopy(self.target.rows.get(model_ref='0').source_data)
        plan,token=self.make_preview(brand='A')
        self.assertEqual(plan['selected'],2);self.assertEqual(plan['matched'],2)
        outcome=apply(self.target.pk,token,self.user)
        self.assertEqual(outcome['calculated'],2)
        self.target.refresh_from_db();self.source.refresh_from_db()
        self.assertEqual(self.target.rates,RATES)
        self.assertEqual(self.target.parameters,PARAMS)
        self.assertEqual(self.target.as_of,date(2026,11,22))
        row=self.target.rows.get(model_ref='0')
        self.assertEqual(row.price,100)
        self.assertEqual(row.source_data,original)
        self.assertEqual(row.overrides['volume'],'2')
        self.assertEqual(row.result['status'],'ready')
        outside=self.target.rows.get(model_ref='1')
        self.assertEqual(outside.overrides,{})
        self.assertEqual(outside.result,{})
        self.assertEqual(self.source.revision,1)
        self.assertEqual(self.source.rows.get(model_ref='0').price,200)

    def test_all_filtered_pages_are_copied(self):
        self.source.rows.all().delete();self.target.rows.all().delete()
        self.rows(self.source,121,True);self.rows(self.target,121)
        plan,token=self.make_preview(brand='A',page=1)
        self.assertEqual(plan['selected'],61)
        result=apply(self.target.pk,token,self.user)
        self.assertEqual(result['copied'],61)
        self.assertEqual(self.target.rows.filter(overrides__volume='2').count(),61)

    def test_global_copy_ignores_row_filter_and_recalculates_all(self):
        plan,token=self.make_preview('globals',brand='No such brand')
        self.assertEqual(plan['selected'],0)
        result=apply(self.target.pk,token,self.user)
        self.target.refresh_from_db()
        self.assertEqual(result['copied'],0);self.assertEqual(result['calculated'],3)
        self.assertEqual(self.target.parameters['price_per_container'],'2000')
        self.assertEqual(self.target.rates['USD'],'120')
        self.assertEqual(self.target.as_of,date(2026,11,22))
        self.assertEqual(self.target.rows.get(model_ref='0').price,100)
        self.assertEqual(self.target.rows.get(model_ref='1').overrides,{})

    def test_combined_copy_does_not_freeze_inherited_global_cost(self):
        _,token=self.make_preview('all',brand='A')
        result=apply(self.target.pk,token,self.user)
        self.assertEqual(result['calculated'],3)
        selected=self.target.rows.get(model_ref='0')
        self.assertNotIn('price_per_container',selected.overrides)
        self.assertEqual(selected.overrides['volume'],'2')
        self.assertEqual(self.target.rows.get(model_ref='1').overrides,{})

    def test_copied_group_keeps_directory_defaults_dynamic(self):
        GoodsGroup.objects.create(name='Other goods',container_volume=100,duty_rate=0)
        row=self.target.rows.get(model_ref='0')
        row.source_data['classification']['group_goods']='Other goods';row.save()
        _,token=self.make_preview('rows',brand='A')
        apply(self.target.pk,token,self.user)
        row.refresh_from_db()
        self.assertEqual(row.overrides['group_goods'],'Goods')
        self.assertNotIn('container_volume',row.overrides)
        self.assertNotIn('duty_percent',row.overrides)

    def test_empty_source_parameters_leave_existing_manual_values(self):
        row=self.target.rows.get(model_ref='0');row.overrides={'purch':'Keep supplier'};row.save()
        _,token=self.make_preview('rows',brand='A')
        apply(self.target.pk,token,self.user)
        row.refresh_from_db();self.assertEqual(row.overrides['purch'],'Keep supplier')

    def test_target_change_after_preview_is_rejected(self):
        _,token=self.make_preview()
        ModelDDPCalculation.objects.filter(pk=self.target.pk).update(revision=2)
        with self.assertRaises(ValidationError):apply(self.target.pk,token,self.user)
        self.assertEqual(self.target.rows.get(model_ref='0').overrides,{})

    def test_no_match_and_bad_source_are_reported(self):
        self.source.rows.filter(model_ref='2').update(overrides={'volume':'-1'})
        self.source.rows.filter(model_ref='1').delete()
        plan,token=self.make_preview()
        self.assertEqual(plan['matched'],1);self.assertEqual(plan['invalid'],1);self.assertEqual(plan['missing'],1)
        result=apply(self.target.pk,token,self.user)
        self.assertEqual(result['copied'],1)

    def test_revision_and_signature_protection(self):
        _,token=self.make_preview()
        with self.assertRaises(ValidationError):apply(self.target.pk,token+'!',self.user)
        other=User.objects.create_user('other-copy-user')
        with self.assertRaises(ValidationError):apply(self.target.pk,token,other)
        self.source.revision=2;self.source.save()
        with self.assertRaises(ValidationError):apply(self.target.pk,token,self.user)
        self.target.refresh_from_db();self.assertEqual(self.target.revision,1)

    def test_directory_change_requires_new_preview(self):
        _,token=self.make_preview()
        GoodsGroup.objects.update(duty_rate=.2)
        with self.assertRaises(ValidationError):apply(self.target.pk,token,self.user)
        self.assertEqual(self.target.rows.get(model_ref='0').overrides,{})

    def test_copy_same_calculation_is_rejected(self):
        request=self.factory.get('/');request.user=self.user
        with self.assertRaises(ValidationError):preview(self.target,self.target,'rows',request)

    def test_empty_filter_is_rejected_for_table_copy(self):
        with self.assertRaises(ValidationError):self.make_preview('rows',brand='Unknown')

    def test_post_preview_and_apply_keep_filters(self):
        url=reverse('model_ddp_detail',args=[self.target.pk])+'?brand=A'
        response=self.client.post(url,{'revision':1,'action':'model_copy_preview','source_calculation':self.source.pk,'mode':'rows'})
        self.assertContains(response,'Копировать и пересчитать')
        self.assertEqual(response.context['model_copy_preview']['selected'],2)
        response=self.client.post(url,{'revision':1,'action':'model_copy_apply','model_copy_token':response.context['model_copy_token']})
        self.assertRedirects(response,url)
        self.target.refresh_from_db();self.assertEqual(self.target.revision,2)

    def test_analyst_cannot_preview_or_apply(self):
        user=User.objects.create_user('copy-analyst')
        UserProjectAccess.objects.create(user=user,project='purchases',role=ANALYST)
        self.client.force_login(user)
        url=reverse('model_ddp_detail',args=[self.target.pk])
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,{'revision':1,'action':'model_copy_preview'}).status_code,403)
