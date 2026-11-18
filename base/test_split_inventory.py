from calendar import monthrange
from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal
import re
import sqlite3
from unittest.mock import Mock,patch
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from django.utils import timezone
from .models import SalesPlanningChannel,SalesPlanningGoods,SalesPlanningGroup,SalesPlanningDirectoryLoad
from .sales_planning_group_service import rebuild_groups
from .split_workflow import create_scenario
from .split_inventory import STOCK_SQL,INCOME_SQL,source_rows,inventory_state,source_rows_many,all_inventory_state


class InventorySourceSQLTests(SimpleTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''
CREATE TABLE goods_fixture (Ссылка,Наименование,БИТ_КлассМощности,БИТ_ТипБлока,Родитель,Даичи_ГруппаПланирования);
CREATE TABLE models (ModelCode,FlagAccountingQtyBA);
CREATE TABLE warehouses (Ссылка,даичи_НазначениеСклада);
CREATE TABLE stream (ReportDate,НоменклатураСсылка,СкладСсылка,ВНаличии,ВПути,КПоступлению,ТипПоступления,ДатаПоступления);
''')
        self.db.executemany('INSERT INTO goods_fixture VALUES (?,?,?,?,?,?)',[
            ('in','IN','07','Внутренний','Бытовые кондиционеры','G'),('out','OUT','07','Наружный','Бытовые кондиционеры','G')])
        self.db.executemany('INSERT INTO models VALUES (?,?)',[('IN',0),('OUT',1),('OUT',1)])
        self.db.executemany('INSERT INTO warehouses VALUES (?,?)',[('sale','Продажа'),('other','Прочее'),('unknown',None)])
        def dateadd(unit,count,raw):
            if raw is None:return None
            day=date.fromisoformat(raw)
            if unit=='DAY':return (day+timedelta(days=count)).isoformat()
            total=day.year*12+day.month-1+count;year,month=divmod(total,12);month+=1
            return date(year,month,min(day.day,monthrange(year,month)[1])).isoformat()
        self.db.create_function('DATEADD',3,dateadd)

    def row(self,model,warehouse,stock=0,transit=0,incoming=0,kind='ПеремещениеТоваров',expected='2026-12-15',report='2026-11-06'):
        self.db.execute('INSERT INTO stream VALUES (?,?,?,?,?,?,?,?)',(report,model,warehouse,stock,transit,incoming,kind,expected))

    def translated(self,sql):
        for source,target in [('DataWH.erp.[Справочники.Номенклатура]','goods_fixture'),('DataWH.dbo.ModelCodeERP','models'),('DataWH.erp.[Справочник.Склады]','warehouses'),('DataWH.erp.StreamGoods','stream')]:sql=sql.replace(source,target)
        sql=sql.replace('COUNT_BIG(*)','COUNT(*)').replace("N'","'").replace('DATEADD(MONTH,',"DATEADD('MONTH',").replace('DATEADD(DAY,',"DATEADD('DAY',")
        return sql

    def query(self,sql):
        return self.db.execute(self.translated(sql),('Бытовые кондиционеры','G','2026-11-06')).fetchall()

    def test_physical_inner_and_outer_both_count_without_flag_or_duplicate_join(self):
        self.row('in','sale',stock=12,transit=3)
        self.row('out','sale',stock=10,transit=2)
        self.row('in','other',stock=100)
        self.row('out','sale',stock=99,report='2026-11-05')
        result={row[0]:row[4] for row in self.query(STOCK_SQL)}
        self.assertEqual(result,{'in':15,'out':12})

    def test_income_types_purposes_and_supplier_date_shift_match_user_rule(self):
        self.row('in','other',transit=2,incoming=4,kind='ПеремещениеТоваров',expected='2026-11-01')
        self.row('out','sale',transit=3,incoming=5,kind='ЗаказПоставщику',expected='2026-11-01')
        self.row('out','sale',incoming=7,kind='ПриобретениеТоваровУслуг',expected='2026-12-15')
        self.row('out','sale',transit=50,kind='ПеремещениеТоваров')
        self.row('out','unknown',transit=60,kind='ПеремещениеТоваров')
        self.row('out','other',incoming=90,kind='Неизвестно')
        rows={(row[0],row[7]):(row[4],row[6]) for row in self.query(INCOME_SQL)}
        self.assertEqual(rows,{('in','ПеремещениеТоваров'):(6,'2026-11-20'),('out','ЗаказПоставщику'):(8,'2027-01-20'),('out','ПриобретениеТоваровУслуг'):(7,'2026-12-15')})

    def test_future_supplier_dates_add_two_months_and_null_dates_remain_unknown(self):
        self.row('in','sale',incoming=3,kind='ЗаказПоставщику',expected='2026-12-31')
        self.row('out','sale',incoming=4,kind='ЗаказПоставщику',expected=None)
        rows={row[0]:row[6] for row in self.query(INCOME_SQL)}
        self.assertEqual(rows,{'in':'2027-02-28','out':None})

    def test_null_quantity_is_not_silently_discarded(self):
        self.row('in','sale',stock=None,transit=3)
        row=self.query(STOCK_SQL)[0];self.assertIsNone(row[4]);self.assertEqual(row[5],1)

    def test_all_group_reads_share_one_connection_and_partition_results(self):
        from types import SimpleNamespace
        self.db.execute('INSERT INTO goods_fixture VALUES (?,?,?,?,?,?)',('h','H-IN','09','Внутренний','Бытовые кондиционеры','H'))
        self.db.execute('INSERT INTO models VALUES (?,?)',('H-IN',0))
        self.row('in','sale',stock=12);self.row('h','sale',stock=30)
        connection,cursor=Mock(),Mock();queries=[];current=[]
        def execute(sql,*args):
            queries.append(args);current[:]=[self.db.execute(self.translated(sql),args)]
        cursor.execute.side_effect=execute
        cursor.fetchone.side_effect=lambda:current[0].fetchone()
        cursor.fetchall.side_effect=lambda:current[0].fetchall()
        groups={'g':{'ref':{'name':'G'}},'h':{'ref':{'name':'H'}}}
        with patch('base.split_inventory.connect_database',return_value=(connection,cursor)) as connect:
            result=source_rows_many(SimpleNamespace(stock_date=date(2026,11,6)),groups)
        connect.assert_called_once();self.assertEqual(len(queries),3)
        self.assertEqual(result['g'][0][0][4],12);self.assertEqual(result['h'][0][0][4],30)
        self.assertEqual(result['g'][1],[]);self.assertEqual(result['h'][1],[])
        self.assertEqual(queries[1],('Бытовые кондиционеры','G','H','2026-11-06'))


class SplitInventoryImportTests(TestCase):
    def setUp(self):
        self.actor=User.objects.create_superuser('inventory-admin');self.client.force_login(self.actor)
        channel=SalesPlanningChannel.objects.create(name='Дилерский')
        SalesPlanningDirectoryLoad.objects.create(directory='goods',loaded_at=timezone.now(),row_count=2)
        for key,kind in [('in','Внутренний'),('out','Наружный')]:
            SalesPlanningGoods.objects.create(source_key=key,name=key.upper(),planning_group='G',product_group='Бытовые кондиционеры',power_class='07',block_type=kind)
        rebuild_groups();group=SalesPlanningGroup.objects.get()
        self.scenario=create_scenario(self.actor,{'title':'SPLIT','start':date(2027,1,1),'end':date(2027,12,31),'stock_date':date(2026,11,6),'groups':[group],'channels':[channel]})
        self.gid=str(group.pk);self.cid=str(channel.pk)
        self.url=reverse('split_scenario',args=[self.scenario.pk])+'?tab=stock'
        self.stock=[('in','IN','07','Внутренний',Decimal(15),0),('out','OUT','07','Наружный',Decimal(12),0)]
        self.incomes=[('in','IN','07','Внутренний',Decimal(6),0,date(2026,11,20),'ПеремещениеТоваров'),
                      ('out','OUT','07','Наружный',Decimal(8),0,date(2027,1,1),'ЗаказПоставщику'),
                      ('out','OUT','07','Наружный',Decimal(7),0,date(2027,12,31),'ПриобретениеТоваровУслуг'),
                      ('out','OUT','07','Наружный',Decimal(2),0,date(2028,1,1),'ЗаказПоставщику')]

    def post(self,revision=None):
        return self.client.post(self.url,{'action':'load_inventory','group':self.gid,'channel':self.cid,'revision':revision or self.scenario.revision})

    def test_import_boundaries_preserves_forecast_buy_channels_and_other_groups(self):
        initial=deepcopy(self.scenario.state);group=initial['groups'][self.gid]
        group['buy']='4';group['forecast']={'2026-11':'2','2026-12':'3'}
        initial['groups']['unrelated']={'ref':{'name':'Other'}}
        self.scenario.state=initial;self.scenario.save(update_fields=['state'])
        with patch('base.split_inventory.source_rows',return_value=(self.stock,self.incomes)):
            response=self.post()
        self.assertEqual(response.status_code,302);self.scenario.refresh_from_db();current=self.scenario.state['groups'][self.gid]
        self.assertEqual(current['powers']['07']['stock_indoor'],'15');self.assertEqual(current['powers']['07']['stock_outdoor'],'12')
        self.assertEqual(current['powers']['07']['prior_indoor'],'6');self.assertEqual(current['powers']['07']['prior_outdoor'],'0')
        self.assertEqual(current['powers']['07']['incoming_outdoor'],'15');self.assertEqual(current['powers']['07']['incoming_indoor'],'0')
        self.assertEqual(current['forecast'],group['forecast']);self.assertEqual(current['buy'],'4');self.assertEqual(current['channels'],group['channels'])
        self.assertEqual(self.scenario.state['groups']['unrelated'],initial['groups']['unrelated'])
        self.assertEqual(current['inventory_source']['after_period'],'2');self.assertEqual(self.scenario.revision,2)

    def test_unknown_income_date_and_changed_model_leave_every_field_unchanged(self):
        before=deepcopy(self.scenario.state)
        for bad in [('out','OUT','07','Наружный',1,0,None,'ЗаказПоставщику'),('unknown','NEW','07','Наружный',1,0,date(2027,1,1),'ЗаказПоставщику')]:
            with patch('base.split_inventory.source_rows',return_value=(self.stock,[bad])):
                response=self.post()
            self.assertEqual(response.status_code,200);self.scenario.refresh_from_db()
            self.assertEqual(self.scenario.state,before);self.assertEqual(self.scenario.revision,1)

    def test_approved_and_stale_revision_never_read_dwh(self):
        with patch('base.split_inventory.source_rows') as source:
            self.post(revision='99');source.assert_not_called()
            self.scenario.status='approved';self.scenario.save(update_fields=['status'])
            self.post();source.assert_not_called()

    def test_missing_snapshot_does_not_fill_zeros_and_closes_connection(self):
        connection,cursor=Mock(),Mock();cursor.fetchone.return_value=(0,)
        with patch('base.split_inventory.connect_database',return_value=(connection,cursor)):
            with self.assertRaises(ValidationError):source_rows(self.scenario,self.scenario.state['groups'][self.gid])
        cursor.close.assert_called_once();connection.close.assert_called_once();self.assertEqual(cursor.execute.call_count,1)

    def test_confirmed_empty_group_loads_explicit_zeros(self):
        with patch('base.split_inventory.source_rows',return_value=([],[])):
            state=inventory_state(self.scenario,self.gid)
        values=state['groups'][self.gid]['powers']['07']
        self.assertTrue(all(values[field+'_'+kind]=='0' for field in ('stock','prior','incoming') for kind in ('indoor','outdoor')))


    def test_all_groups_apply_once_and_any_bad_group_aborts_everything(self):
        other=deepcopy(self.scenario.state['groups'][self.gid]);other['ref'].update(id='other',name='H')
        self.scenario.state['groups']['other']=other;self.scenario.save(update_fields=['state'])
        before=deepcopy(self.scenario.state)
        with patch('base.split_inventory.source_rows_many',return_value={self.gid:(self.stock,self.incomes),'other':([],[])}):
            response=self.client.post(self.url,{'action':'load_inventory_all','revision':1})
        self.assertEqual(response.status_code,302);self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['powers']['07']['stock_indoor'],'15')
        self.assertEqual(self.scenario.state['groups']['other']['powers']['07']['stock_indoor'],'0')
        self.assertEqual(self.scenario.revision,2)
        successful=deepcopy(self.scenario.state)
        bad=[('unknown','BAD','07','Внутренний',1,0)]
        with patch('base.split_inventory.source_rows_many',return_value={self.gid:([],[]),'other':(bad,[])}):
            response=self.client.post(self.url,{'action':'load_inventory_all','revision':2})
        self.assertEqual(response.status_code,200);self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,successful);self.assertEqual(self.scenario.revision,2)

    def test_overview_has_all_groups_channels_and_no_selection_filter(self):
        from .split_stock_overview import overview
        other=deepcopy(self.scenario.state['groups'][self.gid]);other['ref'].update(id='other',name='H')
        self.scenario.state['groups']['other']=other;self.scenario.save(update_fields=['state'])
        result=overview(self.scenario)
        self.assertEqual([row['group']['ref']['name'] for row in result['groups']],['G','H'])
        self.assertEqual([row['channels'][0]['name'] for row in result['groups']],['Дилерский','Дилерский'])
        response=self.client.get(self.url)
        self.assertContains(response,'data-stock-group="other"')
        self.assertNotContains(response,'class="split-picker"')
        self.assertEqual(len(response.context['stock_overview']['groups']),2)


    def test_shared_physical_stock_is_not_multiplied_by_channel_count(self):
        from .split_stock_overview import overview
        with patch('base.split_inventory.source_rows',return_value=(self.stock,[])):
            state=inventory_state(self.scenario,self.gid)
        group=state['groups'][self.gid]
        group['forecast']={'2026-11':'0','2026-12':'0'}
        group['channels'][self.cid]['input']='4'
        state['channels']['second']={'id':'second','name':'Розница'}
        group['channels']['second']=deepcopy(group['channels'][self.cid]);group['channels']['second']['input']='6'
        self.scenario.state=state;result=overview(self.scenario)
        self.assertEqual(result['totals']['stock_indoor'],15);self.assertEqual(result['totals']['stock_outdoor'],12)
        self.assertEqual(result['totals']['sets'],12)
        self.assertEqual([channel['planned'] for channel in result['groups'][0]['channels']],[4,6])

    def test_all_group_load_rejects_stale_and_approved_before_source(self):
        with patch('base.split_inventory.source_rows_many') as source:
            self.client.post(self.url,{'action':'load_inventory_all','revision':99});source.assert_not_called()
            self.scenario.status='approved';self.scenario.save(update_fields=['status'])
            self.client.post(self.url,{'action':'load_inventory_all','revision':1});source.assert_not_called()

    def test_direct_power_forecast_saves_without_plan_shares_and_drives_both_balances(self):
        from .split_workflow import changed_state
        from .split_opening import opening_balance
        from .split_engine import evaluate
        group=self.scenario.state['groups'][self.gid];group['powers']['07']['share']=None
        with patch('base.split_inventory.source_rows',return_value=(self.stock,[])):
            state=inventory_state(self.scenario,self.gid)
        self.scenario.state=state
        post={'action':'stock','group':self.gid,'channel':self.cid,'buy':'0','forecast_mode':'powers','forecast_power_0':'3',
              'stock_indoor_0':'15','stock_outdoor_0':'12','prior_indoor_0':'0','prior_outdoor_0':'0','incoming_indoor_0':'2','incoming_outdoor_0':'1'}
        self.scenario.state=changed_state(self.scenario,post)
        current=self.scenario.state['groups'][self.gid]
        self.assertEqual(current['forecast_mode'],'powers');self.assertEqual(current['powers']['07']['forecast_before'],'3')
        self.assertEqual(opening_balance(self.scenario,current)['totals']['sets'],9)
        result=evaluate(self.scenario)
        self.assertEqual(result['stock'][0]['forecast'],3);self.assertEqual(result['stock'][0]['closing'],9)
        self.assertEqual(current['powers']['07']['incoming_sql_outdoor'],'0')

    def test_monthly_forecast_is_not_erased_by_editing_stock_and_import_can_reset_mode(self):
        from .split_workflow import changed_state
        group=self.scenario.state['groups'][self.gid];group['forecast']={'2026-11':'10','2026-12':'5'}
        group['forecast_imports']={'2026-11':{'start':'2026-11-10','end':'2026-11-30'}}
        state=changed_state(self.scenario,{'action':'stock','group':self.gid,'channel':self.cid,'buy':'0'})
        self.assertEqual(state['groups'][self.gid]['forecast'],group['forecast'])
        self.assertEqual(state['groups'][self.gid]['forecast_imports'],group['forecast_imports'])


    def test_zero_group_forecast_and_zero_plan_need_no_distribution_for_inventory(self):
        from .split_engine import evaluate
        group=self.scenario.state['groups'][self.gid]
        group.update(forecast_mode='group',forecast_before_total='0',buy='0')
        group['powers']['07'].update(share=None,stock_indoor='15',stock_outdoor='12',prior_indoor='0',prior_outdoor='0',incoming_indoor='2',incoming_outdoor='1')
        result=evaluate(self.scenario)
        self.assertEqual(result['stock'][0]['closing'],13)


    def test_missing_plan_quantity_cannot_be_treated_as_zero_for_direct_inventory(self):
        from .split_engine import evaluate
        group=self.scenario.state['groups'][self.gid]
        group.update(forecast_mode='powers',buy='0')
        group['channels'][self.cid]['input']=None
        group['powers']['07'].update(share=None,forecast_before='0',stock_indoor='15',stock_outdoor='12',prior_indoor='0',prior_outdoor='0',incoming_indoor='0',incoming_outdoor='0')
        result=evaluate(self.scenario)
        self.assertIsNone(result['totals']['closing']);self.assertEqual(result['stock'],[])
