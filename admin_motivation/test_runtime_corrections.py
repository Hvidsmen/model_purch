from datetime import date
import calendar
import re
import sqlite3
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, TestCase
from django.core.exceptions import ValidationError, PermissionDenied
from django.contrib.auth.models import User
from django.urls import reverse
from .services.payout_threshold import parameterize
from .services.manager_correction import SQL, patch as correct
from .services.approved_manager_correction import update
from .services.approval import approve_review, review_approval
from . import test_shared_versions
from .models import SubdivisionManagerCoeff, ManagerCorrectionLog, SubdivisionCoeff


class RuntimeTransformTests(SimpleTestCase):
    def test_threshold_changes_payment_guard_without_changing_shares(self):
        source = '''ALTER PROCEDURE motivation.sp_CalcBySub @QuarterShare decimal(9,8)=0.8 AS
SELECT IIF(SUM(Actual) OVER()/SUM(Plan_) OVER()<0.8,0,Amount*@QuarterShare) FROM input;
-- comparison <0.8 stays in comment
'''
        changed = parameterize(source)
        self.assertIn('@QuarterShare decimal(9,8)=0.8', changed)
        self.assertIn('-- comparison <0.8', changed)
        self.assertEqual(changed.count('@PayoutThreshold'), 2)
        query = changed[changed.index('SELECT'):changed.index(';')]
        query = query.replace('@PayoutThreshold', ':threshold').replace('@QuarterShare', ':share')
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE input (Actual real, Plan_ real, Amount real)')
            db.execute('INSERT INTO input VALUES (75,100,100)')
            self.assertEqual(db.execute(query, {'threshold': .8, 'share': .8}).fetchone()[0], 0)
            self.assertEqual(db.execute(query, {'threshold': .7, 'share': .8}).fetchone()[0], 80)
        with self.assertRaises(ValidationError):
            parameterize('ALTER PROC motivation.sp_CalcBySub AS SELECT IIF(Other<0.8,1,0)')

    def test_correction_lookup_obeys_version_period_branch_and_applies_once_to_all_types(self):
        lookup = re.search(r'OUTER APPLY \((SELECT TOP \(1\) c.Coeff_.*?)\) k;', SQL, re.S)[1]
        lookup = lookup.replace('TOP (1) ', '').replace('#portal_manager_correction', 'corrections') + ' LIMIT 1'
        formula = re.search(r'\[СуммаМотивацииUSD\]=(.*?),\n', SQL)[1]
        formula = formula.replace('k.Coeff_', '(' + lookup + ')')
        with sqlite3.connect(':memory:') as db:
            db.create_function('DATEFROMPARTS', 3, lambda y,m,d: date(y,m,d).isoformat())
            db.create_function('EOMONTH', 1, lambda value: date.fromisoformat(value).replace(day=calendar.monthrange(date.fromisoformat(value).year,date.fromisoformat(value).month)[1]).isoformat())
            db.execute('CREATE TABLE corrections (Date_ text, Subdivision text, PeriodId int, Coeff_ real)')
            db.executemany('INSERT INTO corrections VALUES (?,?,?,?)', [
                ('2026-01-01','A',1,.9), ('2026-04-01','A',1,.2),
                ('2026-04-01','A',2,.8), ('2026-01-01','A',5,.7), ('2027-01-01','A',5,.1),
                ('2026-01-01','B',1,.5)])
            db.execute('CREATE TABLE rows (Подразделение text, Год int, СуммаМотивацииUSD real, Type_ text)')
            db.executemany('INSERT INTO rows VALUES (?,?,?,?)', [('A',2026,100,kind) for kind in ('Политики','Продажи','Директора')])
            for period, expected in ((1,90),(2,80),(3,100),(5,70)):
                result = db.execute(f'SELECT {formula} FROM rows r CROSS JOIN (SELECT ? AS PeriodId) p', [period]).fetchall()
                self.assertEqual(result, [(expected,)]*3)
            db.execute("UPDATE rows SET Подразделение='B'")
            self.assertEqual(db.execute(f'SELECT {formula} FROM rows r CROSS JOIN (SELECT 1 AS PeriodId) p').fetchall(), [(50,)]*3)
        self.assertNotIn('SET [СуммаПланUSD]', SQL)
        self.assertEqual(SQL.count('UPDATE r SET'), 1)
        self.assertIn('СуммаМотивацииRURУчетПроцента', SQL)

    def test_correction_is_inserted_before_annual_carry_and_fails_unknown_shapes(self):
        prefix = 'ALTER PROC motivation.sp_CalcBySub @QuarterShare float=.8,@YearShare float=.2 AS SELECT '
        columns = 'СуммаМотивацииUSDУчетПроцента,СуммаМотивацииRURУчетПроцента,СуммаКвМотивацииUSD,СуммаМотивацииUSDГод,AmountRURMotive,КварталМотивации'
        source = prefix + columns + ' INTO DataWH.motivation.ReportMotivationByDealAuthor FROM source; INSERT INTO DataWH.motivation.ReportMotivationByDealAuthor SELECT * FROM carry;'
        changed = correct(source)
        self.assertLess(changed.index('UPDATE r SET'), changed.index('INSERT INTO'))
        with self.assertRaises(ValidationError): correct(changed)
        with self.assertRaises(ValidationError): correct('ALTER PROC p AS SELECT 1')


class ApprovedCorrectionTests(TestCase):
    def setUp(self):
        test_shared_versions.SharedMotivationVersionTests.setUp(self)
        self.actor = User.objects.get(username='portal-test-admin')
        approve_review(review_approval(self.first)['token'])
        self.first.refresh_from_db()

    def test_approved_exception_changes_only_manager_and_logs_before_after(self):
        old_goods = list(SubdivisionCoeff.objects.values_list('pk','motivation_coeff'))
        revision = self.first.revision
        update(self.actor, self.first.pk, self.sub, revision, 'Уточнение после квартала', {str(self.manager_kind.pk): '90%'})
        self.manager.refresh_from_db()
        self.first.refresh_from_db()
        self.assertEqual(self.manager.coeff,.9)
        self.assertEqual(self.first.status,'approved')
        self.assertEqual(self.first.revision,revision+1)
        log = ManagerCorrectionLog.objects.get()
        self.assertEqual(log.actor,self.actor)
        self.assertEqual(log.changes[0]['before'],.8)
        self.assertEqual(log.changes[0]['after'],.9)
        self.assertEqual(list(SubdivisionCoeff.objects.values_list('pk','motivation_coeff')),old_goods)
        with self.assertRaises(ValidationError):
            SubdivisionManagerCoeff.objects.filter(pk=self.manager.pk).update(coeff=.1)
        with self.assertRaises(ValidationError):
            update(self.actor,self.first.pk,self.sub,revision,'Stale',{str(self.manager_kind.pk):'.7'})
        self.assertEqual(ManagerCorrectionLog.objects.count(),1)

    def test_reason_admin_and_confirmation_required(self):
        with self.assertRaises(ValidationError):
            update(self.actor,self.first.pk,self.sub,self.first.revision,'',{str(self.manager_kind.pk):'.9'})
        with self.assertRaises(PermissionDenied):
            update(User.objects.create_user('denied'),self.first.pk,self.sub,self.first.revision,'Reason',{str(self.manager_kind.pk):'.9'})
        url = reverse('sub_act',args=[self.sub.pk])
        data = {'version':self.first.pk,'revision':self.first.revision,'action_button':'update_approved_manager',
                'manager_reason':'Reason',f'kind_coeff={self.manager_kind.pk}':'90%'}
        self.client.post(url,data)
        self.manager.refresh_from_db()
        self.assertEqual(self.manager.coeff,.8)
        self.client.post(url,{**data,'confirm_manager_change':'yes'})
        self.manager.refresh_from_db()
        self.assertEqual(self.manager.coeff,.9)

    def test_manager_export_prepares_runtime_and_rolls_back_on_incompatibility(self):
        from .services.sql_export import export_motivation
        connection, cursor = Mock(), Mock()
        with patch('admin_motivation.services.calculation_jobs.prepare', side_effect=ValidationError('Unsupported')) as prepare:
            with self.assertRaises(ValidationError):
                export_motivation(lambda *args: (connection,cursor))
        prepare.assert_called_once_with(cursor)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
