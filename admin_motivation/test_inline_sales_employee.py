from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from .models import SalesEmployee
from .services import premium_distribution as service


class InlineSalesEmployeeTests(TestCase):
    def setUp(self):
        from .test_distribution_stages import ClosingWorkflowTests
        ClosingWorkflowTests.setUp(self)
        self.batch=ClosingWorkflowTests.create(self)
        self.url=reverse('motivation_distribution_detail',args=[self.batch.pk])

    def fields(self,**extra):
        return {'action':'add_new_person','revision':self.batch.revision,'new_kind':'Продажи','new_payment':'quarter',
                'new_full_name':'  Новый   Сотрудник  ','new_personnel_number':'123','new_weight':'1',
                **{'weight_'+row['id']:'1' for row in self.batch.rows if row['kind']=='Продажи'},**extra}

    def test_creates_directory_employee_and_allocation_on_same_page(self):
        response=self.client.post(self.url,self.fields(new_user=self.admin.pk,new_subdivision=999999))
        self.assertRedirects(response,self.url)
        employee=SalesEmployee.objects.get(full_name='Новый Сотрудник')
        self.assertEqual(employee.subdivision_id,self.branch.pk)
        self.assertEqual(employee.personnel_number,'123')
        self.assertIsNone(employee.user_id)
        self.batch.refresh_from_db()
        sales=[r for r in self.batch.rows if r['kind']=='Продажи']
        self.assertEqual(len(sales),4)
        self.assertEqual(sum(Decimal(r['amount']) for r in sales),100)
        self.assertTrue(all(Decimal(r['amount'])==25 for r in sales))
        self.assertEqual(self.batch.source['workflow']['stage'],'sales')

    def test_invalid_weight_rolls_back_employee_rows_and_revision(self):
        response=self.client.post(self.url,self.fields(new_weight='-1'))
        self.assertEqual(response.status_code,200)
        self.assertFalse(SalesEmployee.objects.filter(full_name='Новый Сотрудник').exists())
        self.batch.refresh_from_db();self.assertEqual(self.batch.revision,1)
        self.assertEqual(len(self.batch.rows),4)
        self.assertContains(response,'value="  Новый   Сотрудник  "')

    def test_duplicate_name_does_not_create_or_silently_choose_employee(self):
        response=self.client.post(self.url,self.fields(new_full_name=' ALICE '))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Такой сотрудник уже есть')
        self.assertEqual(SalesEmployee.objects.count(),3)
        self.batch.refresh_from_db();self.assertEqual(self.batch.revision,1)

    def test_stale_revision_prevents_directory_creation(self):
        response=self.client.post(self.url,self.fields(revision=0))
        self.assertEqual(response.status_code,200)
        self.assertEqual(SalesEmployee.objects.count(),3)

    def test_later_stage_rejects_creation(self):
        from .test_distribution_stages import ClosingWorkflowTests
        self.batch=service.update(self.admin,self.batch.pk,self.batch.revision,ClosingWorkflowTests.changes(self,self.batch),action='sales_complete')
        response=self.client.post(self.url,self.fields())
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Добавление сотрудников доступно на этапе продаж')
        self.assertEqual(SalesEmployee.objects.count(),3)

    def test_sales_ui_has_inline_cards_without_external_directory_navigation(self):
        response=self.client.get(self.url)
        self.assertContains(response,'Создать и добавить в продажи')
        self.assertContains(response,'class="pd-person-card pd-person-card-new"')
        fragment=response.content.decode().split('<section class="pd-people"',1)[1].split('<div class="pd-grid"',1)[0]
        self.assertNotIn('target="_blank"',fragment)
