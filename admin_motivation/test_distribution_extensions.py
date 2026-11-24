from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.contrib.auth.models import User
from openpyxl import Workbook,load_workbook
from .test_premium_distribution import DistributionTests
from .models import SalesEmployee
from .services import premium_distribution as service
from .services.distribution_reporting import apply,total,excel_sheet


class ExtensionTests(DistributionTests):
    def test_employee_scope_and_user_binding_are_protected(self):
        viewer=User.objects.create_user('branch')
        with patch('admin_motivation.sales_employee_views.branches',return_value=['Тест']),patch('admin_motivation.sales_employee_views.role_for',return_value='Директор'):
            from .sales_employee_views import EmployeeForm
            form=EmployeeForm({'full_name':'Сидоров','personnel_number':'12','subdivision':self.branch.pk,'user':self.user.pk},user=viewer)
            self.assertTrue(form.is_valid(),form.errors)
            employee=form.save();self.assertIsNone(employee.user_id)
        self.assertContains(self.client.get(reverse('motivation_sales_employees')),'Сидоров')

    def test_archived_employee_is_available_in_historical_period_only(self):
        employee=SalesEmployee.objects.create(full_name='Уволенный',subdivision=self.branch,is_archived=True,started_on=date(2025,10,1),ended_on=date(2025,11,30))
        self.assertTrue(service.eligible_employees(self.batch).filter(pk=employee.pk).exists())
        self.batch.year=2026
        self.assertFalse(service.eligible_employees(self.batch).filter(pk=employee.pk).exists())

    def test_recipient_without_sales_is_injected_with_three_amounts(self):
        employee=SalesEmployee.objects.create(full_name='Сидоров',subdivision=self.branch)
        b=service.update(self.user,self.batch.pk,1,self.changes(),addition={'employee':employee.pk,'kind':'Продажи','payment':'quarter'})
        change={r['id']:{'amount':r['amount'],'comment':''} for r in b.rows};change['sales']['amount']='60';change[b.rows[-1]['id']]['amount']='40'
        service.update(self.user,b.pk,2,change,action='complete')
        rows=[{'subdivision':'Тест','author':'Иванов','kind':'Продажи','code':'4','period':'4 кв.','currency':'USD','amount':Decimal(100)}]
        apply(rows,self.execution,'Продажи',{'Currency':'USD','Subdivision':['Тест'],'DealAuthor':[]},['4'])
        self.assertEqual(total(rows),100)
        injected=next(r for r in rows if r['author']=='Сидоров')
        self.assertEqual((injected['possible'],injected['amount'],injected['distributed_total']),(0,0,40))
        book=Workbook(write_only=True);excel_sheet(book,rows);out=BytesIO();book.save(out)
        sheet=load_workbook(BytesIO(out.getvalue())).active
        self.assertEqual(sheet['G3'].value,40)

    def test_draft_missing_and_product_slice_are_not_zero_or_leaked(self):
        rows=[{'subdivision':'Тест','author':'Иванов','kind':'Продажи','code':'4'}]
        values={'Currency':'USD','Subdivision':['Тест'],'DealAuthor':['Иванов']}
        apply(rows,self.execution,'Продажи',values,['4']);self.assertIsNone(total(rows))
        service.update(self.user,self.batch.pk,1,self.changes(),action='complete')
        apply(rows,self.execution,'Продажи',values,['4'],product_filtered=True);self.assertIsNone(total(rows))
        apply(rows,self.execution,'Продажи',values,['4']);self.assertEqual(total(rows),100)
        other=[];apply(other,self.execution,'Продажи',{**values,'DealAuthor':['Другой']},['4']);self.assertEqual(other,[])

    def test_reopened_distribution_hides_new_totals_but_old_report_remains(self):
        service.update(self.user,self.batch.pk,1,self.changes(),action='complete')
        service.update(self.user,self.batch.pk,2,{},action='reopen',comment='Исправление')
        rows=[{'subdivision':'Тест','author':'Иванов','kind':'Продажи','code':'4'}]
        apply(rows,self.execution,'Продажи',{'Currency':'USD','Subdivision':['Тест']},['4']);self.assertIsNone(total(rows))
        self.assertContains(self.client.get(reverse('motivation_distribution_detail',args=[self.batch.pk]),{'version':2}),'Завершено')

    def test_shareholder_reports_exclude_distribution_column(self):
        from django.template.loader import render_to_string
        row={'author':'Иванов','distributed':98765,'kinds':['Продажи'],'period_cells':[]}
        section={'kind':'Продажи','page':[row],'rows':[row],'distributed':98765}
        context={'shareholder_report':True,'kind':'team','presentation':{'team_sections':[section],'period_columns':[]},'ledger':{'currency':'USD'},'row':row}
        for template in ('_team_tables.html','_team_ledger.html','_employee_pivot_row.html','_my_periods.html'):
            html=render_to_string('admin_motivation/reports_next/'+template,context)
            self.assertNotIn('После распределения',html)
            self.assertNotIn('98 765',html.replace('\xa0',' '))
