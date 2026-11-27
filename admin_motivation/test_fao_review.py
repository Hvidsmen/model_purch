from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,SimpleTestCase
from django.urls import reverse
from . import test_distribution_stages as fixtures
from .models import FaoReview,FaoDocument,FaoCorrection,PeriodMotivationCoefficient,Subdivision,PremiumDistribution
from .services import fao_review as fao
from .services import fao_sql


def source_rows():
    rows=[]
    for kind in ('Продажи','Политики','Директора'):
        for name,amount,rate in [('A','60','.01'),('B','40','.02')]:
            rows.append({'ТипКоэф':kind,'Номер':'123','ДатаОтгрузки':'2026-05-01','ДатаЗакрытияСделки':'2026-05-20',
                'Год':2026,'Квартал':2,'Месяц':5,'Модель':name,'АвторСделки':'Person','Подразделение':'Closing',
                'СуммаОтгрузокUSD':amount,'СуммаМотивацииUSD':str(Decimal(amount)*Decimal(rate)),
                'AmountRURMotive':str(Decimal(amount)*Decimal(rate)*90),'AmountExwAEUSD':'0','СуммаПланUSD':'0','K0':rate})
    return rows


class ProportionTests(SimpleTestCase):
    def test_original_rates_survive_new_date_and_negative_delta(self):
        document=MagicMock(original_amount=Decimal(100),rows=source_rows())
        rows=fao.scale_rows(document,Decimal(-25),date(2026,8,1))
        for kind in ('Продажи','Политики','Директора'):
            subset=[r for r in rows if r['ТипКоэф']==kind]
            self.assertEqual(sum(Decimal(r['СуммаОтгрузокUSD']) for r in subset),-25)
            self.assertEqual(sum(Decimal(r['СуммаМотивацииUSD']) for r in subset),Decimal('-.35'))
            self.assertEqual(subset[0]['K0'],'.01')
            self.assertEqual(subset[1]['K0'],'.02')
            self.assertEqual(subset[0]['Квартал'],3)
        self.assertEqual(document.rows,source_rows())

    def test_residual_zero_and_signed_source_are_not_arbitrary(self):
        document=MagicMock(original_amount=Decimal(100),rows=source_rows())
        rows=fao.scale_rows(document,Decimal('.01'),date(2026,8,1))
        self.assertEqual(sum(Decimal(r['СуммаОтгрузокUSD']) for r in rows if r['ТипКоэф']=='Продажи'),Decimal('.01'))
        document.original_amount=0
        with self.assertRaises(ValidationError):fao.scale_rows(document,Decimal(1),date(2026,8,1))
        self.assertEqual(fao.scale_rows(document,Decimal(0),date(2026,8,1)),[])

    def test_sql_hook_is_after_rate_selection_before_persisted_fact(self):
        from pathlib import Path
        sql=Path(__file__).parent.joinpath('sql/review/sp_CalcBySub.refactored.sql').read_text()
        patched=fao_sql.patch(sql)
        self.assertLess(patched.index('INTO #fact'),patched.index(fao_sql.MARKER))
        self.assertLess(patched.index(fao_sql.MARKER),patched.index("IF OBJECT_ID('DataWH.motivation.Fact')"))
        self.assertEqual(fao_sql.patch(patched),patched)
        self.assertIn('CROSS APPLY OPENJSON',patched)
        self.assertNotIn('UPDATE DataWH.dbo.Sales',fao_sql.OVERLAY)
        with self.assertRaises(ValidationError):fao_sql.patch('SELECT 1;')
        for variant,table in [('all_2026','FactAll2026'),('separate_2026','FactSeparate2026')]:
            isolated=sql.replace('DataWH.motivation.Fact','DataWH.motivation.'+table)
            adjusted=fao_sql.patch(isolated,variant=variant)
            self.assertIn(fao_sql.MARKER,adjusted)
            if variant=='separate_2026':self.assertIn('PortalSeparateSubdivisions',adjusted)


class FaoWorkflowTests(TestCase):
    setUp=fixtures.ClosingWorkflowTests.setUp

    def document(self,branch=None):
        return FaoDocument.objects.create(subdivision=branch or self.branch,number='123',document_date=date(2026,5,1),
            execution=self.run,rows=source_rows(),original_amount=100,source_hash='a'*64)

    def begin(self):
        with patch('admin_motivation.services.fao_review.timezone.localdate',return_value=date(2026,8,15)):
            return fao.begin(self.finance,self.branch,2026,3,'Сверка ОЗС')

    def adjust(self,review,document,amount,**kwargs):
        with patch('admin_motivation.services.fao_review.timezone.localdate',return_value=date(2026,8,15)):
            return fao.adjust(self.finance,review.pk,review.revision,document.pk,amount,kwargs.get('correction_date',date(2026,8,1)),kwargs.get('excluded',False))

    def test_repeated_delta_exclusion_and_branch_isolation(self):
        review=self.begin();doc=self.document()
        first=self.adjust(review,doc,80)
        self.assertEqual(first.delta,-20)
        review.refresh_from_db()
        second=self.adjust(review,doc,70)
        self.assertEqual(second.delta,-10)  # Not a second -30.
        review.refresh_from_db()
        excluded=self.adjust(review,doc,None,excluded=True)
        self.assertEqual(excluded.delta,-70)
        self.assertEqual(excluded.new_amount,0)
        self.assertTrue(excluded.excluded)
        self.assertEqual(sum(item.delta for item in doc.corrections.all()),-100)
        other=Subdivision.objects.create(subdivision_key='FAO Other',subdivision_name='Other',subdivision_global='Other',chanel=self.branch.chanel)
        review.refresh_from_db()
        with self.assertRaises(FaoDocument.DoesNotExist):self.adjust(review,self.document(other),20)
        self.assertEqual(FaoReview.objects.filter(subdivision=other).count(),0)
        with self.assertRaises(ValidationError):excluded.save()
        with self.assertRaises(ValidationError):excluded.delete()

    def test_closed_second_quarter_is_rejected_open_third_is_allowed(self):
        review=self.begin();doc=self.document()
        with self.assertRaises(ValidationError):self.adjust(review,doc,80,correction_date=date(2026,6,30))
        self.assertEqual(doc.corrections.count(),0)
        self.adjust(review,doc,80,correction_date=date(2026,8,1))
        self.assertEqual(doc.corrections.count(),1)

    def test_permissions_stale_revision_and_finished_review(self):
        with self.assertRaises(PermissionDenied):fao.begin(self.ceo,self.branch,2026,3,'reason')
        review=self.begin();doc=self.document()
        self.adjust(review,doc,80)
        with self.assertRaises(ValidationError):self.adjust(review,doc,70)
        review.refresh_from_db()
        done=fao.finish(self.finance,review.pk,review.revision)
        with self.assertRaises(ValidationError):self.adjust(done,doc,70)

    def test_return_keeps_coefficients_previous_assignments_and_other_branch(self):
        record=PeriodMotivationCoefficient.objects.create(subdivision=self.branch,year=2026,period=3,coefficient='1.2',
            kind_coefficients={'Политики':'1.3'},personal_coefficients={'Политики':{'Person':'1.4'}})
        batch=PremiumDistribution.objects.create(subdivision=self.branch,execution=self.run,year=2026,period=3,currency='USD',
            state='completed',funds=deepcopy(self.funds),rows=deepcopy(self.rows),source={})
        other=Subdivision.objects.create(subdivision_key='FAO Unchanged',subdivision_name='Other',subdivision_global='Other',chanel=self.branch.chanel)
        other_record=PeriodMotivationCoefficient.objects.create(subdivision=other,year=2026,period=3,coefficient=1)
        review=self.begin()
        batch.refresh_from_db();record.refresh_from_db();other_record.refresh_from_db()
        self.assertEqual(batch.state,'archived')
        self.assertEqual(batch.rows,self.rows)
        self.assertEqual(record.coefficient,Decimal('1.2'))
        self.assertEqual(record.personal_coefficients,{'Политики':{'Person':'1.4'}})
        self.assertEqual(other_record.revision,1)
        self.assertEqual(record.history[-1]['distribution'],batch.pk)
        with self.assertRaises(ValidationError):fao.ensure_ready(self.branch,2026,3,self.run)
        review=fao.finish(self.finance,review.pk,review.revision)
        with self.assertRaises(ValidationError):fao.ensure_ready(self.branch,2026,3,self.run)
        self.run.parameters.update(fact_year=2026,calculation_subdivisions='all',premium_amount_quarters=[1,2,3,4])
        self.run.parameters['fao_reviews']={f'{self.branch.pk}:2026:3':review.pk}
        fao.ensure_ready(self.branch,2026,3,self.run)
        next_review=self.begin()
        self.assertNotEqual(next_review.pk,review.pk)
        with self.assertRaises(ValidationError):fao.ensure_ready(self.branch,2026,3,self.run)

    def test_export_only_completed_cycles_and_keeps_original_document_number(self):
        review=self.begin();doc=self.document();item=self.adjust(review,doc,80)
        cursor=MagicMock();fao_sql.sync(cursor);cursor.executemany.assert_not_called()
        review.refresh_from_db();fao.finish(self.finance,review.pk,review.revision)
        fao_sql.sync(cursor)
        rows=cursor.executemany.call_args.args[1]
        self.assertEqual(len(rows),6)
        self.assertIn('ФАО#',rows[0][4])
        self.assertEqual(doc.rows[0]['Номер'],'123')

    def test_finance_http_flow_preview_save_finish_and_stale_post(self):
        self.client.force_login(self.finance)
        url=reverse('motivation_fao_review')
        selection={'subdivision':self.branch.pk,'year':2026,'quarter':3}
        with patch('admin_motivation.services.fao_review.timezone.localdate',return_value=date(2026,8,15)):
            response=self.client.post(url,dict(selection,action='begin',reason='Сверка'))
            self.assertEqual(response.status_code,302)
            review=FaoReview.objects.get()
            document=self.document()
            with patch('admin_motivation.fao_views.lookup',return_value=document):
                response=self.client.post(url,dict(selection,action='lookup',number='123',document_date='2026-05-01'))
            self.assertContains(response,'Исходная сумма, USD')
            body=dict(selection,action='preview',review=review.pk,revision=review.revision,document=document.pk,new_amount='80',correction_date='2026-08-01')
            preview=self.client.post(url,body)
            self.assertEqual(preview.context['preview']['delta'],-20)
            self.assertEqual(FaoCorrection.objects.count(),0)
            body['action']='adjust'
            self.assertEqual(self.client.post(url,body).status_code,302)
            self.client.post(url,body)  # Same stale revision must not duplicate delta.
            self.assertEqual(FaoCorrection.objects.count(),1)
            review.refresh_from_db()
            with patch('admin_motivation.services.fao_review.recalculate') as recalculate:
                response=self.client.post(url,dict(selection,action='finish',review=review.pk,revision=review.revision))
                self.assertEqual(response.status_code,302)
                recalculate.assert_called_once()
            review.refresh_from_db();self.assertEqual(review.state,'completed')

    def test_wrong_branch_http_post_is_rejected_without_mutation(self):
        self.client.force_login(self.finance)
        review=self.begin();document=self.document()
        other=Subdivision.objects.create(subdivision_key='Other FAO HTTP',subdivision_name='Other',subdivision_global='Other',chanel=self.branch.chanel)
        response=self.client.post(reverse('motivation_fao_review'),{'subdivision':other.pk,'year':2026,'quarter':3,'action':'adjust',
            'review':review.pk,'revision':review.revision,'document':document.pk,'new_amount':'80','correction_date':'2026-08-01'})
        self.assertEqual(response.status_code,404)
        self.assertEqual(FaoCorrection.objects.count(),0)

    def test_actor_labels_survive_user_deletion(self):
        review=self.begin();doc=self.document();item=self.adjust(review,doc,80)
        label=self.finance.get_username()
        self.finance.delete()
        item.refresh_from_db();review.refresh_from_db()
        self.assertIsNone(item.actor_id)
        self.assertEqual(item.actor_label,label)
        self.assertEqual(review.opened_label,label)

    def test_finance_page_access_and_exact_branch_document_scope(self):
        self.client.force_login(self.finance)
        response=self.client.get(reverse('motivation_fao_review'))
        self.assertContains(response,'Проверка ОЗС ФАО')
        self.client.force_login(self.ceo)
        self.assertEqual(self.client.get(reverse('motivation_fao_review')).status_code,403)
