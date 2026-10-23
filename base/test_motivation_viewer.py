from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User, Group
from .access import MOTIVATION_VIEWER, ADMIN, ROLES, can_access, role_for, ensure_roles
from .models import PortalReportGroup
from admin_motivation import test_shared_versions
from admin_motivation.models import MotivationCalculationSchedule


class MotivationViewerTests(TestCase):
    def setUp(self):
        test_shared_versions.SharedMotivationVersionTests.setUp(self)
        self.actor=self.client.session['_auth_user_id']
        ensure_roles()
        self.viewer=User.objects.create_user('all-motivation-viewer')
        self.viewer.groups.add(Group.objects.get(name=MOTIVATION_VIEWER))
        self.client.force_login(self.viewer)

    def test_role_choices_and_group_inheritance_without_department_restrictions(self):
        from .admin import PortalUserChangeForm
        from .group_views import ReportGroupForm
        self.assertIn((MOTIVATION_VIEWER,MOTIVATION_VIEWER),PortalUserChangeForm().fields['portal_role'].choices)
        self.assertIn((MOTIVATION_VIEWER,MOTIVATION_VIEWER),ReportGroupForm().fields['portal_role'].choices)
        self.viewer.groups.clear()
        group=Group.objects.create(name='All motivation read only')
        PortalReportGroup.objects.create(group=group,portal_role=MOTIVATION_VIEWER)
        self.viewer.groups.add(group)
        viewer=User.objects.get(pk=self.viewer.pk)
        self.assertEqual(role_for(viewer),MOTIVATION_VIEWER)
        self.assertTrue(can_access(viewer,'motivation'))
        self.assertFalse(can_access(viewer,'admin'))
        self.assertFalse(can_access(viewer,'purchases'))
        self.assertFalse(viewer.is_staff)

    def test_all_motivation_read_pages_are_available(self):
        routes=[('global_coeff_admin_motivation',[]),('motivation_discount_segments',[]),
                ('coeff_subdivisions_admin_motivation',[self.sub.pk]),('coeff_subdivisions_admin_motivation',[self.other.pk]),
                ('motivation_sales_plans',[]),('motivation_reports',[]),('motivation_plan_summary',[]),
                ('motivation_director_directory',[]),('motivation_copy_group',[]),('loader_motive',[]),('motivation_calculations',[]),
                ('motivation_subdivision_merge',[]),('motivation_director_coeff_import',[])]
        for name,args in routes:
            with self.subTest(name=name,args=args):
                response=self.client.get(reverse(name,args=args),{'version':self.first.pk})
                self.assertEqual(response.status_code,200)
                self.assertContains(response,'Только просмотр')
        response=self.client.get(reverse('global_coeff_admin_motivation'))
        self.assertFalse(response.context['version_editable'])
        self.assertContains(response,'motivation-viewer-fields')
        self.assertContains(response,'Сегменты скидок')

    def test_no_other_project_or_portal_administration_access(self):
        for route in ('scenario_list','portal_users','portal_user_groups'):
            self.assertEqual(self.client.get(reverse(route)).status_code,403)
        self.assertEqual(self.client.get('/admin/').status_code,403)

    def test_all_mutations_denied_before_views_and_sql(self):
        job=MotivationCalculationSchedule.objects.create(name='Observe only')
        requests=[('gb_act',[],{'action_button':'save_coeff'}),('sub_act',[self.sub.pk],{'action_button':'save_coeff'}),
            ('loader_motive',[],{}),('motivation_calculations',[],{'job':job.pk,'action':'run'}),
            ('motivation_subdivision_merge',[],{'action':'apply'}),('motivation_discount_segments',[],{'action':'import'}),
            ('motivation_sales_plans',[],{'action':'create'}),('motivation_director_directory',[],{'action':'delete'}),
            ('motivation_director_coeff_import',[],{}),('motivation_copy_group',[],{}),
            ('motivation_bulk_formula',[],{}),('motivation_version_reopen',[],{})]
        with patch('admin_motivation.services.calculation_jobs.start_job') as start,patch('admin_motivation.services.sql_export.export_motivation') as export:
            for name,args,values in requests:
                with self.subTest(name=name):
                    self.assertEqual(self.client.post(reverse(name,args=args),{'version':self.first.pk,**values}).status_code,403)
            start.assert_not_called();export.assert_not_called()
        self.global_coeff.refresh_from_db();self.assertEqual(self.global_coeff.motivation_coeff,.1)
        for method in ('put','patch','delete'):
            self.assertEqual(getattr(self.client,method)(reverse('gb_act')).status_code,403)

    def test_saved_comparison_allowed_but_proposals_and_approval_forbidden(self):
        url=reverse('motivation_coefficient_comparison')
        with patch('admin_motivation.comparison_views.compare_plan',return_value={'ok':True}) as compare:
            # No plan is a validation error, not a permission denial.
            self.assertEqual(self.client.post(url,{'action':'compare','version':self.first.pk},content_type='application/json').status_code,400)
            for values in ({'action':'approve'},{'action':'review'},{'action':'compare','changes':{'1':'.2'}},{'action':'compare','deleted':[1]},{'action':'compare','director_changes':{'1:0':'.2'}}):
                self.assertEqual(self.client.post(url,values,content_type='application/json').status_code,403)
            compare.assert_not_called()

    def test_viewer_monitor_is_read_only_and_history_visible(self):
        job=MotivationCalculationSchedule.objects.create(name='Monitor')
        with patch('admin_motivation.services.calculation_jobs.monitor',return_value={'status':'Завершено','history':[{'message':'Visible history'}]}),patch('admin_motivation.services.execution_tracking.observe') as observe:
            response=self.client.get(reverse('motivation_calculation_monitor',args=[job.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['history'][0]['message'],'Visible history')
        observe.assert_called_once()
        self.assertEqual(observe.call_args.kwargs,{'persist':False})

    def test_admin_can_preview_viewer_without_post_mutation(self):
        self.client.force_login(User.objects.get(pk=self.actor))
        self.client.post(reverse('portal_user_preview'),{'user':self.viewer.pk})
        response=self.client.get(reverse('global_coeff_admin_motivation'))
        self.assertTrue(response.context['portal_motivation_readonly'])
        self.assertEqual(self.client.post(reverse('loader_motive')).status_code,403)

    def test_reading_actual_execution_does_not_write_observations(self):
        import json
        from datetime import datetime
        from uuid import uuid4
        from unittest.mock import Mock
        from admin_motivation.models import MotivationExecution
        from admin_motivation.services.execution_tracking import observe
        job=MotivationCalculationSchedule.objects.create(name='Pure read')
        connection,cursor=Mock(),Mock()
        cursor.fetchone.return_value=(uuid4(),job.key,datetime(2026,1,1),datetime(2026,1,1,1),'completed',json.dumps({'fact_year':2026,'plan_version':'Observed plan'}),'{}')
        execution=observe(job,lambda *args:(connection,cursor),persist=False)
        self.assertEqual(execution.parameters['plan_version'],'Observed plan')
        self.assertFalse(MotivationExecution.objects.exists())
        self.assertIsNone(execution.pk)
