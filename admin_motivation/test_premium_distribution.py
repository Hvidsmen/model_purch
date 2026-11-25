from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
import uuid
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from base.testing import authorize_admin
from .models import Chanel,Subdivision,MotivationExecution,PremiumDistribution,SalesEmployee
from .services import premium_distribution as service


class DistributionTests(TestCase):
    def setUp(self):
        self.user=authorize_admin(self.client)
        channel=Chanel.objects.create(chanel_name='Распределение')
        self.branch=Subdivision.objects.create(subdivision_key='Тест',subdivision_name='Тест',subdivision_global='Тест',chanel=channel)
        self.execution=MotivationExecution.objects.create(run_id=uuid.uuid4(),job_key=uuid.uuid4(),state='completed',started_at=timezone.now(),completed_at=timezone.now(),parameters={'fact_year':2025,'variant':'main'})
        self.funds=[{'kind':'Продажи','payment':'quarter','amount':'100.00'},{'kind':'Политики','payment':'quarter','amount':'50.00'}]
        self.rows=[{'id':'sales','author':'Иванов','kind':'Продажи','payment':'quarter','original':'100.00','amount':'100.00','comment':''},
                   {'id':'pol','author':'Петров','kind':'Политики','payment':'quarter','original':'50.00','amount':'50.00','comment':''}]
        from .services.closing_coefficient import confirm
        confirm(self.user,self.branch,2025,4,1)
        with patch.object(service,'read_source',return_value=(self.funds,self.rows)):
            self.batch=service.create(self.user,self.execution,self.branch,4,'USD')
        # Existing allocations retain their original amount-entry workflow.
        self.batch.source.pop('workflow');self.batch.rows=self.rows;self.batch.save()

    def changes(self):return {r['id']:{'amount':r['amount'],'comment':r['comment']} for r in self.batch.rows}

    def test_source_snapshot_and_unique_currency_independent_close(self):
        self.execution.parameters={'fact_year':2030};self.execution.save()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.source['parameters']['fact_year'],2025)
        with self.assertRaises(ValidationError):service.create(self.user,self.batch.execution,self.branch,4,'RUB')

    def test_funds_cannot_cross_and_completion_is_atomic(self):
        change=self.changes();change['sales']['amount']='110';change['pol']['amount']='40'
        with self.assertRaises(ValidationError):service.update(self.user,self.batch.pk,1,change,action='complete')
        self.batch.refresh_from_db();self.assertEqual(self.batch.revision,1)
        self.assertEqual(self.batch.rows[0]['amount'],'100.00')

    def test_stale_revision_rejected(self):
        service.update(self.user,self.batch.pk,1,self.changes())
        with self.assertRaises(ValidationError):service.update(self.user,self.batch.pk,1,self.changes())

    def test_completed_locked_and_reopened_versions_keep_finance_export(self):
        complete=service.update(self.user,self.batch.pk,1,self.changes(),action='complete')
        with self.assertRaises(ValidationError):service.update(self.user,self.batch.pk,2,self.changes())
        service.update(self.user,self.batch.pk,2,{},action='reopen',comment='Исправление')
        url=reverse('motivation_distribution_detail',args=[self.batch.pk])
        result=self.client.get(url,{'version':2,'export':'xlsx'})
        self.assertEqual(result.status_code,200)
        book=load_workbook(BytesIO(b''.join(result.streaming_content)))
        self.assertIn('?version=2',book.active['B2'].value)
        self.assertEqual(book.active['H5'].value,100)
        self.assertEqual(self.client.get(url,{'export':'xlsx'}).status_code,403)
        self.assertEqual(complete.events.get(revision=2).snapshot['state'],'completed')

    def test_new_person_and_optional_comments_for_politicians(self):
        employee=SalesEmployee.objects.create(full_name='Сидоров',subdivision=self.branch)
        batch=service.update(self.user,self.batch.pk,1,self.changes(),addition={'employee':employee.pk,'kind':'Политики','payment':'quarter'})
        self.batch=batch; change=self.changes();change['pol']['amount']='40';new=batch.rows[-1]['id'];change[new]['amount']='10'
        change['pol']['amount']='39'
        with self.assertRaises(ValidationError):service.update(self.user,batch.pk,2,change,action='complete')
        change['pol']['amount']='40'
        completed=service.update(self.user,batch.pk,2,change,action='complete')
        self.assertEqual(completed.state,'completed')
        self.assertTrue(all(not r['comment'] for r in completed.rows))

    def test_finite_cent_precision_and_negative_amounts(self):
        for value in ('NaN','Infinity','1.001','1e999','-1'):
            change=self.changes();change['sales']['amount']=value
            with self.assertRaises(ValidationError):service.update(self.user,self.batch.pk,1,change)

    def test_scope_and_financier_read_only(self):
        viewer=User.objects.create_user('finance')
        with patch.object(service,'role_for',return_value='Финансист'):
            service.authorize(viewer,self.branch)
            with self.assertRaises(PermissionDenied):service.update(viewer,self.batch.pk,1,self.changes())
        with patch.object(service,'role_for',return_value='Директор'),patch.object(service,'politician_scope',return_value={'all_authors':True,'subdivision':['Другой']}):
            with self.assertRaises(PermissionDenied):service.authorize(viewer,self.branch)

    def test_source_reads_only_period_and_separates_annual_catchup(self):
        with patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()),patch('admin_motivation.services.reports.load_report') as load:
            load.return_value={'period_summary':{'columns':[{'label':'Год'}],'rows':[{'subdivision':'Тест','author':'Иванов','values':[Decimal('80.234')],'remainder':Decimal('12.345')}]}}
            funds,rows=service.read_source(self.execution,self.branch,5,'USD')
        self.assertEqual([(f['payment'],f['amount']) for f in funds],[('annual','80.23'),('catchup','12.35')]*2)
        self.assertEqual(len(rows),4)
        self.assertEqual(load.call_args.kwargs['premium_period'],['5'])

    def test_future_period_blocked_and_forged_row_ids_rejected(self):
        self.execution.parameters={'fact_year':2030};self.execution.save()
        with self.assertRaises(ValidationError):service.create(self.user,self.execution,self.branch,1,'USD')
        with self.assertRaises(ValidationError):service.update(self.user,self.batch.pk,1,{'forged':{'amount':'100'}})

    def test_real_start_form_post_and_template(self):
        url=reverse('motivation_distributions')
        self.assertContains(self.client.get(url),'Распределение премий')
        self.assertContains(self.client.get(reverse('motivation_distribution_detail',args=[self.batch.pk])),'Завершить распределение')
        with patch('admin_motivation.services.execution_tracking.latest_for_report',return_value=self.execution):
            response=self.client.post(url,{'subdivision':self.branch.pk,'period':'4','currency':'USD'})
        self.assertContains(response,'уже создано')
