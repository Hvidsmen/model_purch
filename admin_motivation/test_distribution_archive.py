from copy import deepcopy
from io import BytesIO
from openpyxl import load_workbook
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth.models import User,Group
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.urls import reverse
from base.access import DIRECTOR,BRANCH_DIRECTOR,FINANCIER
from . import test_premium_distribution as fixtures
from .models import PremiumDistribution
from .services import premium_distribution as service
from .services.closing_coefficient import confirm
from .services.distribution_reporting import apply,total


class DistributionArchiveTests(TestCase):
    setUp=fixtures.DistributionTests.setUp
    changes=fixtures.DistributionTests.changes

    def new_batch(self):
        with patch.object(service,'read_source',return_value=(deepcopy(self.funds),deepcopy(self.rows))):
            return service.create(self.user,self.execution,self.branch,4,'USD')

    def report(self):
        rows=[{'subdivision':'Тест','author':'Иванов','kind':'Продажи','code':'4'}]
        apply(rows,self.execution,'Продажи',{'Currency':'USD','Subdivision':['Тест']},['4'])
        return total(rows)

    def test_delete_draft_keeps_audit_and_frees_period_and_coefficient(self):
        old=self.batch
        service.retire(self.user,old.pk,old.revision,'delete_draft','Ошибка периода')
        old.refresh_from_db();self.assertEqual(old.state,'deleted')
        self.assertEqual(old.events.get(action='delete_draft').comment,'Ошибка периода')
        from .services.manager_periods import context
        self.assertFalse(context(self.user,None,self.branch,[],2025)['manager_period_rows'][3]['locked'])
        confirm(self.user,self.branch,2025,4,'1.2',revision=1)
        new=self.new_batch();self.assertNotEqual(new.pk,old.pk)
        with self.assertRaises(ValidationError):self.new_batch()
        with self.assertRaises(ValidationError):service.update(self.user,old.pk,old.revision,{},action='reopen',comment='Повтор')

    def test_archive_completed_stops_reporting_and_preserves_historical_excel(self):
        batch=service.update(self.user,self.batch.pk,1,self.changes(),action='complete')
        self.assertEqual(self.report(),100)
        with self.assertRaises(ValidationError):service.retire(self.user,batch.pk,batch.revision,'delete_draft','Нельзя удалить')
        service.retire(self.user,batch.pk,batch.revision,'archive','Отменить проведение')
        self.assertIsNone(self.report())
        batch.refresh_from_db();self.assertEqual(batch.state,'archived')
        self.assertEqual(batch.events.get(revision=2).snapshot['state'],'completed')
        url=reverse('motivation_distribution_detail',args=[batch.pk])
        response=self.client.get(url,{'version':2})
        self.assertContains(response,'Распределение больше не применяется')
        self.assertNotContains(response,'value="return_sales"')
        response=self.client.get(url,{'version':2,'export':'xlsx'})
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(b''.join(response.streaming_content)))
        self.assertIn('больше не применяется',book.active['I1'].value)
        response.close()
        new=self.new_batch()
        service.retire(self.user,new.pk,new.revision,'archive','Второй архив')
        self.assertIsNotNone(self.new_batch())

    def test_admin_only_archive_and_branch_director_delete_scope(self):
        user=User.objects.create_user('retire-director');user.groups.add(Group.objects.get(name=DIRECTOR))
        with self.assertRaises(PermissionDenied):service.retire(user,self.batch.pk,1,'archive','Причина')
        branch_user=User.objects.create_user('retire-branch');branch_user.groups.add(Group.objects.get(name=BRANCH_DIRECTOR))
        with patch.object(service,'branches',return_value=['Другое']):
            with self.assertRaises(PermissionDenied):service.retire(branch_user,self.batch.pk,1,'delete_draft','Чужое')
        with patch.object(service,'branches',return_value=['Тест']):
            batch=service.retire(branch_user,self.batch.pk,1,'delete_draft','Мой черновик')
        self.assertEqual(batch.state,'deleted')

    def test_revision_reason_and_terminal_states_are_protected(self):
        for revision,reason in ((0,'Причина'),(1,''),(1,'x'*2001)):
            with self.assertRaises(ValidationError):service.retire(self.user,self.batch.pk,revision,'archive',reason)
        service.retire(self.user,self.batch.pk,1,'archive','В архив')
        self.batch.refresh_from_db()
        for action in ('archive','delete_draft'):
            with self.assertRaises(ValidationError):service.retire(self.user,self.batch.pk,self.batch.revision,action,'Повтор')
        with self.assertRaises(ValidationError):service.update(self.user,self.batch.pk,self.batch.revision,self.changes(),action='complete')

    def test_index_post_and_archive_list(self):
        url=reverse('motivation_distributions')
        with patch('admin_motivation.premium_distribution_views.latest_calculation',return_value=None):
            self.assertContains(self.client.get(url,{'year':self.batch.year,'period':self.batch.period}),'Подтвердить удаление')
            response=self.client.post(url,{'action':'archive','batch':self.batch.pk,'revision':1,'reason':'Отмена'})
            self.assertEqual(response.status_code,302)
            self.assertNotContains(self.client.get(url,{'year':self.batch.year,'period':self.batch.period}),'Подтвердить удаление')
            self.assertContains(self.client.get(url,{'archive':1,'year':self.batch.year,'period':self.batch.period}),'Архив')
            finance=User.objects.create_user('retire-finance');finance.groups.add(Group.objects.get(name=FINANCIER))
            self.client.force_login(finance)
            response=self.client.post(url,{'action':'delete_draft','batch':self.batch.pk,'revision':2,'reason':'Отмена'})
            self.assertEqual(response.status_code,403)

    def test_database_prevents_two_active_allocations(self):
        with self.assertRaises(IntegrityError),transaction.atomic():
            PremiumDistribution.objects.create(subdivision=self.branch,execution=self.execution,year=2025,period=4,currency='RUB')
