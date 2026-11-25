from django.test import TestCase
from django.urls import reverse
from .services import premium_distribution as service
from .models import SalesEmployee


class DistributionStageLayoutTests(TestCase):
    def setUp(self):
        from .test_distribution_stages import ClosingWorkflowTests
        ClosingWorkflowTests.setUp(self)

    def create(self):
        from .test_distribution_stages import ClosingWorkflowTests
        return ClosingWorkflowTests.create(self)

    def changes(self,batch):
        from .test_distribution_stages import ClosingWorkflowTests
        return ClosingWorkflowTests.changes(self,batch)

    def test_sales_stage_shows_only_sales_and_visible_add_controls_before_table(self):
        batch=self.create()
        response=self.client.get(reverse('motivation_distribution_detail',args=[batch.pk]))
        self.assertEqual(response.status_code,200)
        self.assertTrue(all(r['kind']=='Продажи' for r in response.context['display_rows']))
        self.assertTrue(all(f['kind']=='Продажи' for f in response.context['funds']))
        self.assertNotContains(response,'<td>Политики</td>')
        self.assertNotContains(response,'Политики · Квартальная премия')
        self.assertNotContains(response,'<th>Комментарий</th>')
        html=response.content.decode()
        self.assertIn('<details open class="pd-fund"',html)
        self.assertLess(html.index('Добавить сотрудника продаж'),html.index('class="pd-grid"'))
        self.assertContains(response,'Зафиксировать премии продаж')

    def test_politician_stage_hides_sales_weights_and_add_person_controls(self):
        batch=self.create()
        batch=service.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='sales_complete')
        response=self.client.get(reverse('motivation_distribution_detail',args=[batch.pk]))
        self.assertTrue(all(r['kind']=='Политики' for r in response.context['display_rows']))
        self.assertTrue(all(f['kind']=='Политики' for f in response.context['funds']))
        self.assertNotContains(response,'name="weight_')
        self.assertNotContains(response,'Добавить выбранного сотрудника')
        self.assertContains(response,'name="amount_pol"')

    def test_existing_batch_can_add_a_new_sales_employee_and_fix_their_premium(self):
        batch=self.create()
        employee=SalesEmployee.objects.create(full_name='New sales employee',subdivision=self.branch)
        url=reverse('motivation_distribution_detail',args=[batch.pk])
        response=self.client.get(url)
        self.assertEqual([e.pk for e in response.context['employees']],[employee.pk])
        fields={'revision':batch.revision,'action':'add_person','new_employee':employee.pk,'new_kind':'Продажи','new_payment':'quarter'}
        response=self.client.post(url,fields)
        self.assertEqual(response.status_code,302)
        batch.refresh_from_db()
        added=next(r for r in batch.rows if r.get('employee_id')==employee.pk)
        changes=self.changes(batch)
        for row in batch.rows:changes[row['id']]['weight']='1' if row['id']==added['id'] else '0'
        batch=service.update(self.admin,batch.pk,batch.revision,changes,action='sales_complete')
        added=next(r for r in batch.rows if r.get('employee_id')==employee.pk)
        self.assertEqual(added['amount'],'100')
        self.assertEqual(batch.source['workflow']['stage'],'policies')

    def test_completed_distribution_still_shows_both_kinds(self):
        batch=self.create()
        batch=service.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='sales_complete')
        batch=service.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='complete')
        response=self.client.get(reverse('motivation_distribution_detail',args=[batch.pk]))
        self.assertEqual({r['kind'] for r in response.context['display_rows']},{'Продажи','Политики'})
        self.assertContains(response,'Скачать Excel для финансистов')
