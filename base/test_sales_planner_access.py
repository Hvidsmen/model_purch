from copy import deepcopy
from datetime import date
from django.test import TestCase
from django.contrib.auth.models import User,Group
from django.urls import reverse
from unittest.mock import patch
from . import test_split_power_grid as fixtures
from .access import ADMIN,PLANNER,role_for,can_access
from .models import UserProjectAccess,GroupProjectAccess,SalesPlanningGoods,SalesPlanningGroup,SplitScenario
from .sales_planning_group_service import rebuild_groups,group_key
from .split_workflow import group_snapshot
from .group_views import ReportGroupForm

class SalesPlannerAccessTests(TestCase):
 def setUp(self):
  fixtures.PowerGridTests.setUp(self)
  self.admin=self.actor
  SalesPlanningGoods.objects.update(sales_plan_group='1. RAC')
  for i,kind in enumerate(('Внутренний','Наружный')):SalesPlanningGoods.objects.create(source_key='foreign'+str(i),name='FOREIGN MODEL '+str(i),planning_group='Foreign group',product_group='Бытовые кондиционеры',sales_plan_group='2. VRF',power_class='07',block_type=kind)
  rebuild_groups();self.foreign=SalesPlanningGroup.objects.get(source_key=group_key('Foreign group'))
  own=self.scenario.state['groups'][self.gid];own['ref']['details']['sales_plan_group']='1. RAC';self.scenario.save(update_fields=['state'])
  foreign_state=deepcopy(self.scenario.state);foreign_state['groups']={str(self.foreign.pk):{**group_snapshot(self.foreign),'channels':deepcopy(own['channels'])}}
  self.foreign_scenario=SplitScenario.objects.create(title='PRIVATE VRF PLAN',year=2027,start=self.scenario.start,end=self.scenario.end,stock_date=self.scenario.stock_date,created_by=self.admin,state=foreign_state)
  self.planner=User.objects.create_user('planner')
  self.grant=UserProjectAccess.objects.create(user=self.planner,project='sales_planning',role=PLANNER,sales_plan_groups=['1. RAC'])
  self.client.force_login(self.planner)
 def test_navigation_catalogs_and_direct_urls_obey_responsibility(self):
  home=self.client.get(reverse('index_base'));self.assertContains(home,'Сценарии, продажи, распределение, запасы и поставки.');self.assertNotContains(home,'Роль не назначена')
  response=self.client.get(reverse('sales_planning_split'));self.assertEqual(response.status_code,200)
  self.assertContains(response,self.scenario.title);self.assertNotContains(response,'PRIVATE VRF PLAN')
  response=self.client.get(reverse('sales_planning_goods'));self.assertNotContains(response,'FOREIGN MODEL')
  response=self.client.get(reverse('sales_planning_groups'));self.assertNotContains(response,'Foreign group')
  self.assertEqual(self.client.get(reverse('sales_planning_group_detail',args=[self.foreign.pk])).status_code,404)
  foreign_url=reverse('split_scenario',args=[self.foreign_scenario.pk])
  for query in ('','?tab=stock','?export=xlsx','?export=dwh'):self.assertEqual(self.client.get(foreign_url+query).status_code,403)
  self.assertEqual(self.client.get(self.url).status_code,200)
 def test_forged_composition_and_import_cannot_change_other_scopes(self):
  before=deepcopy(self.scenario.state);revision=self.scenario.revision
  response=self.client.post(self.url,{'action':'add_group','revision':revision,'new_group':self.foreign.pk})
  self.assertEqual(response.status_code,403)
  self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before);self.assertEqual(self.scenario.revision,revision)
  with patch('base.sales_planning.load_goods') as load:
   self.assertEqual(self.client.post(reverse('sales_planning_goods'),{'action':'load_goods'}).status_code,403);load.assert_not_called()
  self.assertEqual(self.client.post(self.url,{'action':'approve','revision':revision}).status_code,403)
 def test_personal_override_group_union_and_empty_scope_fail_closed(self):
  self.grant.delete();group=Group.objects.create(name='VRF planners');self.planner.groups.add(group)
  GroupProjectAccess.objects.create(group=group,project='sales_planning',role=PLANNER,sales_plan_groups=['2. VRF'])
  self.assertEqual(self.client.get(self.url).status_code,403)
  self.assertEqual(self.client.get(reverse('split_scenario',args=[self.foreign_scenario.pk])).status_code,200)
  grant=UserProjectAccess.objects.create(user=self.planner,project='sales_planning',role=PLANNER,sales_plan_groups=['1. RAC'])
  self.assertEqual(self.client.get(self.url).status_code,200)
  grant.sales_plan_groups=[];grant.save();self.assertEqual(self.client.get(self.url).status_code,403)
 def test_new_scenario_carries_scope_and_shared_foreign_archives_are_denied(self):
  response=self.client.post(reverse('sales_planning_split'),{'title':'RAC draft','start':'2027-01-01','end':'2027-12-31','stock_date':'2026-12-31'})
  self.assertEqual(response.status_code,302)
  self.assertEqual(self.client.get(response.url).status_code,200)
  self.scenario.state['archived_groups']={str(self.foreign.pk):next(iter(self.foreign_scenario.state['groups'].values()))}
  self.scenario.save(update_fields=['state']);self.assertEqual(self.client.get(self.url).status_code,403)
 def test_admin_can_assign_project_role_and_group_scope(self):
  form=ReportGroupForm({'name':'RAC team','sales_planning_role':PLANNER,'sales_plan_groups':['1. RAC'],'members':[self.planner.pk]},actor=self.admin)
  self.assertTrue(form.is_valid(),form.errors)
  group=form.save();grant=group.project_access.get(project='sales_planning')
  self.assertEqual(grant.sales_plan_groups,['1. RAC'])
  empty=ReportGroupForm({'name':'Empty team','sales_planning_role':PLANNER},actor=self.admin)
  self.assertFalse(empty.is_valid());self.assertIn('sales_plan_groups',empty.errors)
  self.client.force_login(self.admin)
  response=self.client.get(reverse('portal_user_groups'),{'project':'sales_planning'})
  self.assertContains(response,'Модель планирования продаж');self.assertContains(response,'Планировщик')

 def test_project_administrator_does_not_gain_global_administration(self):
  self.grant.role=ADMIN;self.grant.sales_plan_groups=[];self.grant.save()
  self.assertEqual(self.client.get(reverse('split_scenario',args=[self.foreign_scenario.pk])).status_code,200)
  self.assertEqual(self.client.get(reverse('portal_user_groups')).status_code,403)
  response=self.client.get(reverse('sales_planning_goods'))
  self.assertContains(response,'FOREIGN MODEL');self.assertContains(response,'name="action" value="load_goods"')

 def test_group_scope_union_and_mixed_scenario_require_all_categories(self):
  self.scenario.state['groups'].update(self.foreign_scenario.state['groups']);self.scenario.save(update_fields=['state'])
  self.assertEqual(self.client.get(self.url).status_code,403)
  self.grant.delete()
  for category in ('1. RAC','2. VRF'):
   group=Group.objects.create(name=category);self.planner.groups.add(group)
   GroupProjectAccess.objects.create(group=group,project='sales_planning',role=PLANNER,sales_plan_groups=[category])
  self.assertEqual(self.client.get(self.url).status_code,200)

 def test_personal_user_form_saves_scope_and_explicit_denial_overrides_groups(self):
  from .user_views import UserForm
  form=UserForm({'username':self.planner.username,'is_active':'on','sales_planning_role':PLANNER,'sales_plan_groups':['2. VRF']},instance=self.planner,actor=self.admin)
  self.assertTrue(form.is_valid(),form.errors);form.save()
  self.assertEqual(UserProjectAccess.objects.get(user=self.planner,project='sales_planning').sales_plan_groups,['2. VRF'])
  self.assertEqual(self.client.get(self.url).status_code,403)
  form=UserForm({'username':self.planner.username,'is_active':'on','sales_planning_role':''},instance=self.planner,actor=self.admin)
  self.assertTrue(form.is_valid(),form.errors);form.save()
  self.assertEqual(self.client.get(reverse('sales_planning_split')).status_code,403)

 def test_stock_selection_filters_source_groups_before_returning_metadata(self):
  from unittest.mock import MagicMock
  from .split_stock_groups import groups_from_stock
  connection=MagicMock();cursor=MagicMock();cursor.fetchone.return_value=(2,)
  own=next(iter(self.scenario.state['groups'].values()))
  cursor.fetchall.return_value=[(own['ref']['name'],10,0),('Foreign group',30,0)]
  with patch('base.split_stock_groups.connect_database',return_value=(connection,cursor)):
   result=groups_from_stock(self.scenario.stock_date,allowed_group_ids={self.gid})
  self.assertEqual([row['id'] for row in result['groups']],[self.gid])

 def test_restricted_history_never_uses_a_broader_foreign_basis(self):
  from .sales_planning_access import check_scenario
  from .split_distribution_history import resolve_proposal,NoDistributionBase
  from django.core.exceptions import ValidationError
  check_scenario(self.planner,self.scenario)
  group=self.scenario.state['groups'][self.gid]
  with patch('base.split_distribution_history.load_basis',side_effect=NoDistributionBase('empty')) as source:
   with self.assertRaises(ValidationError):resolve_proposal(self.scenario,group,'ДИЛЕРСКИЙ','months')
  self.assertTrue(source.call_args_list)
  self.assertTrue(all(call.kwargs['scope']=='planning_group' for call in source.call_args_list))
