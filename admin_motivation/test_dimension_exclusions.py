import re,sqlite3
from decimal import Decimal as D
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User,Group
from base.testing import authorize_test_case
from base.access import MOTIVATION_VIEWER
from .models import MotivationPlanningGroupExclusion as Groups,MotivationChannelExclusion as Channels
from .services.subdivision_scope import exclusion_scope,policy_signature,included
from .services.motivation_exclusions import allowed,sql_allowed,amounts_for,rules
from . import test_sales_plans as fixtures


class DimensionRuleTests(TestCase):
    def test_initial_rules_and_empty_tables(self):
        self.assertEqual(list(Groups.objects.values_list('coefficient_type','value','enabled')),[('Политики','HEAT',True)])
        self.assertEqual(list(Channels.objects.values_list('coefficient_type','value','enabled')),[('Политики','ДПП',True)])
        with exclusion_scope():
            self.assertFalse(allowed('Политики',' 5. heat ','Dealer'))
            self.assertFalse(allowed('Политики','1. RAC',' дпп '))
            for kind in ('Продажи','Директора'):self.assertTrue(allowed(kind,'5. HEAT','ДПП'))
        Groups.objects.all().delete();Channels.objects.all().delete()
        with exclusion_scope():
            self.assertEqual(sql_allowed(),'1=1')
            self.assertTrue(allowed('Политики','5. HEAT','ДПП'))
            self.assertFalse(included('ДПП'))  # The separate subdivision-name rule remains.

    def test_sql_and_python_agree_for_independent_coefficient_rules(self):
        Groups.objects.create(coefficient_type='Продажи',value=' 2. VRF ')
        Channels.objects.create(coefficient_type='Директора',value='ДПП')
        rows=[(kind,pg,channel) for kind in ('Политики','Продажи','Директора')
              for pg in ('1. RAC','5. HEAT','2. vrf') for channel in ('Dealer',' дпп ')]
        with exclusion_scope(),sqlite3.connect(':memory:') as db:
            db.create_function('UPPER',1,lambda v:v.upper() if v else v)
            db.create_function('PATINDEX',2,lambda pat,v:next((i+1 for i,c in enumerate(v) if c not in '0123456789. '),0))
            db.execute('CREATE TABLE data(ТипКоэф,ГруппаПлановПродаж,Канал)')
            db.executemany('INSERT INTO data VALUES (?,?,?)',rows)
            actual=db.execute(('SELECT * FROM data WHERE '+sql_allowed()).replace("N'","'")).fetchall()
            self.assertCountEqual(actual,[row for row in rows if allowed(*row)])
            director_sql=sql_allowed('Директора').replace("N'","'")
            self.assertCountEqual(db.execute('SELECT * FROM data WHERE '+director_sql).fetchall(),
                                  [row for row in rows if allowed('Директора',row[1],row[2])])

    def test_rule_edits_invalidate_scope_and_do_not_change_current_operation(self):
        with exclusion_scope():
            original=policy_signature()
            Groups.objects.filter(value='HEAT').update(enabled=False)
            self.assertFalse(allowed('Политики','HEAT'))
        with exclusion_scope():
            self.assertTrue(allowed('Политики','5. HEAT'))
            self.assertNotEqual(policy_signature(),original)
            self.assertNotIn("N'HEAT'",sql_allowed())
            from .services.coefficient_iteration_2025 import build_sql
            source=build_sql()
            self.assertNotIn('__MOTIVATION_DIMENSION_SCOPE__',source)
            self.assertNotIn("=N'HEAT'",source)

    def test_seed_preserves_disabled_rules_and_quotes_values(self):
        Groups.objects.update(enabled=False)
        seed=import_module('admin_motivation.migrations.0045_motivationchannelexclusion_and_more').seed
        seed(apps,SimpleNamespace(connection=connection));seed(apps,SimpleNamespace(connection=connection))
        self.assertEqual(Groups.objects.count(),1);self.assertFalse(Groups.objects.get().enabled)
        Channels.objects.create(coefficient_type='Продажи',value="O'CHANNEL")
        with exclusion_scope():self.assertIn("N'O''CHANNEL'",sql_allowed())

    def test_changed_channel_rules_require_provenance_in_legacy_plans(self):
        from django.core.exceptions import ValidationError
        Channels.objects.create(coefficient_type='Продажи',value='Dealer')
        segments={f'O{i}':'10' for i in range(5)}
        with exclusion_scope():
            with self.assertRaisesMessage(ValidationError,'перезагрузите план'):
                amounts_for('Продажи',segments,'RAC')
            segments['channels']={'Dealer':['10']*5};segments['channels_complete']=True
            self.assertEqual(amounts_for('Продажи',segments,'RAC'),[D(0)]*5)

    def test_actual_directors_keep_their_base_when_sales_are_excluded(self):
        from unittest.mock import Mock
        from .test_coefficient_iteration_calculation import inputs,row,MOSCOW,TT
        from .services.coefficient_iteration_calculation import RateBook
        from .services.coefficient_iteration_runner import directors_2025
        Groups.objects.create(coefficient_type='Продажи',value='RAC')
        Channels.objects.create(coefficient_type='Директора',value='ДПП')
        cursor=Mock();cursor.fetchall.return_value=[(MOSCOW,1,'1. RAC','Бытовые кондиционеры','DAIKIN','O1',D(100),D(0))]
        plans=[{**row(MOSCOW,2025),'channel':'Dealer'},{**row(TT,2025),'channel':'ДПП'}]
        snapshot={'directors':[],'corrections':[],'aliases':{},'default':'Подразделение(Глобально)'}
        with exclusion_scope():
            result,_=directors_2025(cursor,plans,RateBook(*inputs()),{'quarter_share':'80','year_share':'20','payout_threshold':'.8'},snapshot)
        self.assertEqual([(r['employee'],r['total']) for r in result],[('Чевокин',D(40))])
        sql=cursor.execute.call_args.args[0]
        self.assertIn("=N'Продажи'",sql)
        self.assertIn("=N'ДПП'",sql)
        self.assertNotIn("=N'RAC'",sql)

    def test_director_actuals_are_not_lost_when_only_plan_groups_are_excluded(self):
        from unittest.mock import Mock
        from .test_coefficient_iteration_calculation import inputs,row,MOSCOW
        from .services.coefficient_iteration_calculation import RateBook
        from .services.coefficient_iteration_runner import directors_2025
        Groups.objects.create(coefficient_type='Директора',value='RAC')
        cursor=Mock();cursor.fetchall.return_value=[(MOSCOW,1,'5. HEAT','*','*','O1',D(100),D(0))]
        snapshot={'directors':[],'corrections':[],'aliases':{},'default':'Подразделение(Глобально)'}
        with exclusion_scope():
            result,_=directors_2025(cursor,[row(MOSCOW,2025)],RateBook(*inputs()),{'quarter_share':'80','year_share':'20','payout_threshold':'.8'},snapshot)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['possible'][0],D(32))
        self.assertEqual(result[0]['quarters'][0],D(32))


class DimensionPlanTests(TestCase):
    def setUp(self):
        self.fixture=fixtures.SalesPlanTests();self.fixture.setUp()
        self.fixture.cursor.description += [('SubdivisionKey',),('Chanel',)]
        a,b=self.fixture.rows()
        self.fixture.cursor.fetchall.return_value=[(*a,'A-DEALER','Dealer'),('A',*b[1:],'A-DPP','ДПП')]
        from .services.sales_plans import load_plan
        load_plan(self.fixture.scenario.pk,self.fixture.connector)
        self.plan=self.fixture.scenario

    def test_rule_changes_reuse_full_loaded_base_and_restore_excluded_channels(self):
        from .services.sales_plans import calculate_plan
        from .services.plan_summary import summary_report
        calculate_plan(self.plan.pk)
        self.assertEqual(self.plan.lines.get(subdivision='').policies_usd,D(100))
        Channels.objects.filter(coefficient_type='Политики').update(enabled=False)
        calculate_plan(self.plan.pk)
        self.assertEqual(self.plan.lines.get(subdivision='').policies_usd,D(150))
        Groups.objects.create(coefficient_type='Продажи',value='SALES')
        calculate_plan(self.plan.pk)
        line=self.plan.lines.get(subdivision='')
        self.assertEqual(line.amount_usd,D(1500));self.assertEqual(line.sales_usd,D(0))
        self.assertEqual(line.policies_usd,D(150))
        self.assertEqual(summary_report(self.plan,{})['summary_nodes'][0]['global'],D(150))
        Groups.objects.filter(coefficient_type='Продажи').update(enabled=False)
        calculate_plan(self.plan.pk)
        self.assertEqual(self.plan.lines.get(subdivision='').sales_usd,D(75))

    def test_private_full_plan_applies_director_rules_independently_of_sales(self):
        from .test_coefficient_iteration_calculation import inputs
        from .services.coefficient_iteration_calculation import RateBook,at_full_plan,plan_rows
        Channels.objects.create(coefficient_type='Директора',value='ДПП')
        Groups.objects.create(coefficient_type='Продажи',value='SALES')
        payload,mappings,_,people=inputs()
        self.plan.refresh_from_db()
        with exclusion_scope():
            rates=RateBook(payload,mappings,{1:'A-DEALER',2:'A-DPP'},people,{1:'A',2:'A'})
            rows,_=at_full_plan(plan_rows(self.plan,2026),rates,2027,D(80),D(20))
        self.assertNotIn('Продажи',{row['kind'] for row in rows})
        self.assertEqual(sum(row['total'] for row in rows if row['kind']=='Директора'),D(700))
        self.assertEqual(next(row['total'] for row in rows if row['kind']=='Политики'),D(100))


class DimensionDirectoryTests(TestCase):
    def setUp(self):authorize_test_case(self)

    def test_both_directories_are_in_menu_and_editor_can_add_sales_rule(self):
        for dimension in ('groups','channels'):
            url=reverse('motivation_'+dimension+'_exclusion_directory')
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'Исключения групп планов продаж')
            self.assertContains(response,'Исключения каналов')
        url=reverse('motivation_groups_exclusion_directory');existing=Groups.objects.get()
        data={'rules-TOTAL_FORMS':'2','rules-INITIAL_FORMS':'1',
            'rules-0-id':str(existing.pk),'rules-0-coefficient_type':'Политики','rules-0-value':'HEAT','rules-0-enabled':'on',
            'rules-1-coefficient_type':'Продажи','rules-1-value':' 2. vrf ','rules-1-enabled':'on'}
        self.assertRedirects(self.client.post(url,data),url)
        self.assertTrue(Groups.objects.filter(coefficient_type='Продажи',value='VRF',enabled=True).exists())
        data.update({'rules-1-coefficient_type':'Политики','rules-1-value':'5. HEAT'})
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Такое исключение')

    def test_reader_cannot_modify_rules(self):
        user=User.objects.create_user('dimension-reader');user.groups.add(Group.objects.get(name=MOTIVATION_VIEWER));self.client.force_login(user)
        for dimension in ('groups','channels'):
            response=self.client.post(reverse('motivation_'+dimension+'_exclusion_directory'),{})
            self.assertEqual(response.status_code,403)
        self.assertEqual(Groups.objects.count(),1);self.assertEqual(Channels.objects.count(),1)
