from datetime import date
from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.core.exceptions import ValidationError, PermissionDenied
from django.contrib.auth.models import User
from base.testing import authorize_test_case
from . import test_shared_versions
from .models import GlobalCoeffVersion, VariationCalculate, GlobalCoeff, SubdivisionCoeff, VersionReopenLog, DirectorEmployee, SubdivisionDirectorCoeff
from .services.pilot_formula import GLOBAL_SUBDIVISION, patch_calculation
from .services.bulk_formula import apply_formula
from .services.version_reopen import reopen_version
from .services.versions import create_version
from .services.approval import review_approval, approve_review


class SqlPilotTransformationTests(SimpleTestCase):
    def test_global_ratio_and_gates_do_not_change_personal_volume(self):
        sql = """SELECT PersonalVolume * Coefficient AS PersonalMotivation,
 IIF(CASE WHEN VariationCalculate='Подразделение' THEN
 SUM(Actual) OVER(PARTITION BY Подразделение,АвторСделки,ТипКоэф)
 / SUM(Planned) OVER(PARTITION BY Подразделение,АвторСделки,ТипКоэф)
 ELSE 0 END < .8 OR
 SUM(Actual) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
 / SUM(Planned) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф) < .8,0,PersonalVolume * Coefficient) FROM Source;"""
        result = patch_calculation(sql)
        self.assertIn("WHEN VariationCalculate = N'Подразделение(Глобально)'", result)
        self.assertIn('OVER(PARTITION BY Подразделение,ТипКоэф,Год)',result)
        self.assertIn("IIF(VariationCalculate=N'Подразделение(Глобально)',SUM(Actual) OVER(PARTITION BY Подразделение,Год,ТипКоэф),SUM(Actual) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф))",result)
        self.assertEqual(result.count('PersonalVolume * Coefficient'),2)
        self.assertNotIn('PARTITION BY CASE',result)
        self.assertNotIn(GLOBAL_SUBDIVISION, sql)

    def test_subdivision_execution_includes_non_pilot_goods_but_pays_personal_volume(self):
        import sqlite3
        sql = """SELECT АвторСделки,
 IIF(CASE WHEN VariationCalculate='Подразделение' THEN
 SUM(Actual) OVER(PARTITION BY Подразделение,АвторСделки,ТипКоэф)
 / SUM(Planned) OVER(PARTITION BY Подразделение,АвторСделки,ТипКоэф)
 ELSE 0 END < .8 OR
 SUM(Actual) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
 / SUM(Planned) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф) < .8,0,PersonalVolume * Coefficient) AS Paid FROM Source"""
        with sqlite3.connect(':memory:') as database:
            database.execute('CREATE TABLE Source (Подразделение TEXT, АвторСделки TEXT, Год INT, ТипКоэф TEXT, VariationCalculate TEXT, Actual REAL, Planned REAL, PersonalVolume REAL, Coefficient REAL)')
            database.executemany('INSERT INTO Source VALUES (?,?,?,?,?,?,?,?,?)',[
                ('A','Low',2026,'Политики',GLOBAL_SUBDIVISION,50,100,50,.1),
                ('A','High',2026,'Политики','Подразделение',350,300,350,.1),
                ('A','OtherYear',2025,'Политики','Подразделение',0,999,0,.1),
                ('B','OtherBranch',2026,'Политики','Подразделение',0,999,0,.1),
            ])
            patched = patch_calculation(sql).replace("N'", "'")  # SQLite has no T-SQL Unicode literal prefix.
            self.assertEqual(dict(database.execute(patched))['Low'],5)
            database.execute("UPDATE Source SET VariationCalculate='Подразделение' WHERE АвторСделки='Low'")
            self.assertEqual(dict(database.execute(patched))['Low'],0)

    def test_dashboard_pilot_percent_uses_subdivision_but_keeps_personal_amount(self):
        from .services.reports import manager_report
        rows=[{'VariationCalculate':GLOBAL_SUBDIVISION,'ГруппаПлановПродаж':'RAC',
            'СуммаМотивацииUSDQ':5,'СуммаМотивацииUSDУчетПроцентаQ':5,
            'СуммаПланUSDКварталValueQ':100,'СуммаОтгрузокUSDКварталValueQ':50,
            'СуммаПланUSD_S_Q':400,'СуммаОтгрузокUSD_S_Q':400}]
        report=manager_report(rows)
        self.assertEqual(report['totals'][0],5)
        self.assertEqual(report['totals'][2],1)
        self.assertEqual(report['totals'][3],5)
        self.assertEqual(report['sections'][0]['values'][2],1)
        self.assertEqual(report['total_blocks'][5]['value'],400)

    def test_unrecognized_calculation_fails_closed(self):
        for sql in ('SELECT 1', "CASE WHEN VariationCalculate='Подразделение' THEN 1 ELSE 0 END"):
            with self.assertRaises(ValidationError): patch_calculation(sql)


class PilotSettingsTests(TestCase):
    def setUp(self):
        test_shared_versions.SharedMotivationVersionTests.setUp(self)
        self.formula = VariationCalculate.objects.get(variation_name=GLOBAL_SUBDIVISION)
        self.actor = self.client.session['_auth_user_id']
        self.user = User.objects.get(pk=self.actor)

    def test_bulk_formula_scope_and_stale_revision(self):
        self.first.refresh_from_db()
        initial = self.local_coeff.motivation_coeff
        apply_formula(self.first.pk, self.first.revision, [self.good.pk], self.formula.pk, ['Политики'], self.sub.pk)
        self.local_coeff.refresh_from_db()
        self.assertEqual(self.local_coeff.variation_calculate,self.formula)
        self.assertEqual(self.local_coeff.motivation_coeff,initial)
        self.assertNotEqual(self.global_coeff.variation_calculate_id,self.formula.pk)
        self.assertNotEqual(SubdivisionCoeff.objects.get(subdivision=self.other,goods=self.good).variation_calculate_id,self.formula.pk)
        with self.assertRaises(ValidationError):
            apply_formula(self.first.pk, self.first.revision, [self.good.pk], self.formula.pk, ['Политики'], self.sub.pk)

    def test_rate_edit_preserves_different_formulas_for_politicians_and_sales(self):
        from .models import TypeCoeff
        sales = GlobalCoeff.objects.create(version=self.first,goods=self.good,type_coeff=TypeCoeff.objects.get(type_coeff_name='Продажи'),segment=self.segment,variation_calculate=self.variation,motivation_coeff=.2,manager_coeff=1)
        self.first.refresh_from_db()
        apply_formula(self.first.pk,self.first.revision,[self.good.pk],self.formula.pk,['Политики'])
        response=self.client.post(reverse('gb_act'),{'action_button':'save_coeff','version':self.first.pk,
            f'select_var_calc={self.good.pk}':self.formula.pk,f'original_var_calc={self.good.pk}':self.formula.pk,
            f'global_coeff={self.global_coeff.pk}':'.15',f'global_coeff={sales.pk}':'.25'})
        self.assertEqual(response.status_code,302)
        sales.refresh_from_db()
        self.global_coeff.refresh_from_db()
        self.assertEqual(sales.variation_calculate_id,self.variation.pk)
        self.assertEqual(self.global_coeff.variation_calculate_id,self.formula.pk)
        self.assertEqual(sales.motivation_coeff,.25)

    def test_pilot_export_prepares_portal_procedure_in_same_transaction(self):
        from .services.sql_export import export_motivation
        self.first.refresh_from_db()
        apply_formula(self.first.pk,self.first.revision,[self.good.pk],self.formula.pk,['Политики'],self.sub.pk)
        approve_review(review_approval(GlobalCoeffVersion.objects.get(pk=self.first.pk))['token'])
        connection,cursor=Mock(),Mock()
        with patch('admin_motivation.services.calculation_jobs.prepare') as prepare:
            export_motivation(Mock(return_value=(connection,cursor)))
            prepare.assert_called_once_with(cursor)
        connection.commit.assert_called_once()
        connection.reset_mock()
        with patch('admin_motivation.services.calculation_jobs.prepare',side_effect=ValidationError('Unsupported procedure')):
            with self.assertRaises(ValidationError): export_motivation(Mock(return_value=(connection,cursor)))
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_reopen_snapshots_and_preserves_approval_history(self):
        approve_review(review_approval(self.first)['token'])
        self.first.refresh_from_db()
        reopened = reopen_version(self.user, self.first.pk, self.first.revision, 'Correction')
        self.assertTrue(reopened.is_editable)
        log = VersionReopenLog.objects.get(version=self.first)
        self.assertEqual(log.snapshot['admin_motivation.GlobalCoeff'][0]['motivation_coeff'], .1)
        self.assertEqual(log.snapshot['version']['status'], 'approved')
        from .models import MotivationApproval
        self.assertTrue(MotivationApproval.objects.filter(version=self.first).exists())
        self.global_coeff.motivation_coeff = .5
        self.global_coeff.save()
        log.refresh_from_db()
        self.assertEqual(log.snapshot['admin_motivation.GlobalCoeff'][0]['motivation_coeff'], .1)
        response = self.client.get(reverse('motivation_version_reopen_snapshot',args=[log.pk]))
        self.assertEqual(response.status_code,200)

    def test_reopen_blocks_new_draft_stale_revision_and_non_admin(self):
        approve_review(review_approval(self.first)['token'])
        self.first.refresh_from_db()
        with self.assertRaises(ValidationError): reopen_version(self.user,self.first.pk,-1,'Correction')
        with self.assertRaises(PermissionDenied): reopen_version(User.objects.create_user('outsider'),self.first.pk,self.first.revision,'Correction')
        create_version(date(2026,1,1),source_version=self.first.pk)
        with self.assertRaisesMessage(ValidationError,'более новый черновик'):
            reopen_version(self.user,self.first.pk,self.first.revision,'Correction')

    def test_reopen_restores_replaced_timeline(self):
        approve_review(review_approval(self.first)['token'])
        draft = create_version(date(2001,1,1),source_version=self.first.pk,allow_overwrite=True)
        approve_review(review_approval(draft)['token'],confirm_overwrite=True)
        draft.refresh_from_db()
        reopen_version(self.user,draft.pk,draft.revision,'Correction')
        self.first.refresh_from_db()
        self.assertEqual(self.first.status,'approved')
        self.assertIsNone(self.first.replaced_by_id)

    def test_director_deletion_requires_confirmation_and_preserves_coefficients(self):
        url = reverse('motivation_director_directory')
        director = DirectorEmployee.objects.create(full_name='Unused')
        self.client.post(url,{'action':'delete','employee':director.pk})
        self.assertTrue(DirectorEmployee.objects.filter(pk=director.pk).exists())
        self.client.post(url,{'action':'delete','employee':director.pk,'confirm':'yes'})
        self.assertFalse(DirectorEmployee.objects.filter(pk=director.pk).exists())
        director = DirectorEmployee.objects.create(full_name='Used')
        director.subdivisions.add(self.sub)
        SubdivisionDirectorCoeff.objects.create(version=self.first,subdivision=self.sub,employee=director,goods=self.good)
        response=self.client.post(url,{'action':'delete','employee':director.pk,'confirm':'yes'},follow=True)
        self.assertContains(response,'Директор удалён из справочника.')
        director.refresh_from_db()
        self.assertTrue(director.is_archived)
        self.assertFalse(director.subdivisions.exists())
        self.assertNotContains(response, '>Used<')
        self.assertTrue(SubdivisionDirectorCoeff.objects.filter(employee=director).exists())
        from .director_forms import DirectorEmployeeForm
        form = DirectorEmployeeForm({'full_name': 'Revived', 'subdivisions': [self.sub.pk]}, instance=director)
        self.assertFalse(form.is_valid())
        self.assertEqual(self.client.get(url, {'edit_employee': director.pk}).status_code, 404)
        self.assertTrue(DirectorEmployee.objects.filter(pk=director.pk).exists())

    def test_reopened_version_can_be_approved_again_without_losing_history(self):
        from .models import MotivationApproval
        approved=approve_review(review_approval(self.first)['token'])
        first_approval=MotivationApproval.objects.get(version=approved)
        reopened=reopen_version(self.user,approved.pk,approved.revision,'Change coefficients')
        self.global_coeff.refresh_from_db()
        self.global_coeff.motivation_coeff=.2;self.global_coeff.save()
        reopened.refresh_from_db()
        reviewed=review_approval(reopened)
        response=self.client.post(reverse('motivation_coefficient_comparison'),data={'action':'approve','token':reviewed['token'],'confirm_overwrite':True},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        reopened.refresh_from_db();self.assertEqual(reopened.status,'approved')
        self.assertEqual(MotivationApproval.objects.filter(version=approved).count(),2)
        first_approval.refresh_from_db()
        self.assertEqual(MotivationApproval.objects.filter(version=approved).order_by('pk').first().pk,first_approval.pk)
        self.assertEqual(VersionReopenLog.objects.get(version=approved).snapshot['admin_motivation.GlobalCoeff'][0]['motivation_coeff'],.1)

    def test_sqlite_busy_approval_returns_retry_message(self):
        from django.db import OperationalError
        with patch('admin_motivation.comparison_views.approve_review',side_effect=OperationalError('database is locked')):
            response=self.client.post(reverse('motivation_coefficient_comparison'),data={'action':'approve','token':'test'},content_type='application/json')
        self.assertEqual(response.status_code,409)
        self.assertIn('База сейчас занята',response.json()['error'])
