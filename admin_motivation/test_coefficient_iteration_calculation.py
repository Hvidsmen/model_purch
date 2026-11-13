from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from io import BytesIO
from unittest.mock import Mock,patch
from django.core.exceptions import ValidationError
from django.test import TestCase,SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User
from openpyxl import load_workbook
from base.testing import authorize_test_case
from .models import (CoefficientIteration,CoefficientIterationRun,SalesPlanScenario,SalesPlanLine,
 Subdivision,Chanel,DirectorEmployee,GlobalCoeffVersion)
from .services.coefficient_iteration_calculation import RateBook,at_full_plan,plan_rows,MOSCOW,TT,MERGED
from .services.coefficient_iteration_runner import start,execute,aggregate_rows,SCENARIOS


def inputs():
    dimensions=[['1. RAC','Бытовые кондиционеры','DAIKIN'],['5. HEAT','*','*']]
    def block(kind,labels,value):
        return {'kind':kind,'column':6,'labels':labels,'entries':[{'key':key,'rates':[str(value)]*5,'row':i+6} for i,key in enumerate(dimensions)]}
    payload={'branches':{'name':'branches.xlsx','warnings':[],'blocks':[block('Политики',[MOSCOW],'.1'),block('Продажи',[MOSCOW],'.2')]},
             'directors':{'name':'directors.xlsx','warnings':[],'blocks':[block('Директора',['Волков М'],'.3'),block('Директора',['Гадайда Д'],'.4')]}}
    mappings={'branches_0_0':{'subdivision':1},'branches_1_0':{'subdivision':1},
        'directors_0_0':{'employee':1,'employee_2025':3,'subdivisions':[1]},
        'directors_1_0':{'employee':2,'employee_2025':4,'subdivisions':[1]}}
    return payload,mappings,{1:MOSCOW,2:TT},{1:'Волков',2:'Галайда',3:'Кудицкая',4:'Чевокин'}


def row(sub=MOSCOW,year=2027,month=1,amount='100',heat=False):
    return {'date':date(year,month,1),'branch':sub,'planning':'5. HEAT' if heat else '1. RAC',
            'goods':'*' if heat else 'Бытовые кондиционеры','brand':'*' if heat else 'DAIKIN',
            'amount':D(amount),'segments':[D(0),D(amount),D(0),D(0),D(0)]}


class MoneyTests(SimpleTestCase):
    def book(self):return RateBook(*inputs())

    def test_2027_merges_inputs_but_counts_each_director_once(self):
        rows,details=at_full_plan([row(),row(TT,amount='50')],self.book(),2027,D(80),D(20))
        self.assertEqual({r['branch'] for r in rows},{MERGED})
        policy=next(r for r in rows if r['kind']=='Политики')
        self.assertEqual(policy['quarters'],[D(12),0,0,0]);self.assertEqual(policy['annual_paid'],D(3));self.assertEqual(policy['total'],D(15))
        directors={r['employee']:r['total'] for r in rows if r['kind']=='Директора'}
        self.assertEqual(directors,{'Волков':D(45),'Галайда':D(60)})
        self.assertEqual(len(details),4)

    def test_2026_only_q4_and_annual_only_from_q4(self):
        rows,_=at_full_plan([row(year=2026,month=1,amount='1000'),row(year=2026,month=10),row(TT,2026,10,'50')],self.book(),2026,D(80),D(20))
        policy=next(r for r in rows if r['kind']=='Политики')
        self.assertEqual(policy['quarters'],[0,0,0,D(12)])
        self.assertEqual(policy['annual_quarters'],[0,0,0,D(3)])
        with self.assertRaises(ValidationError):at_full_plan([row(year=2026,month=10)],self.book(),2026,D(80),D(20))

    def test_heat_is_excluded_only_for_policies(self):
        rows,_=at_full_plan([row(heat=True)],self.book(),2027,D(80),D(20))
        self.assertNotIn('Политики',[r['kind'] for r in rows])
        self.assertEqual(next(r['total'] for r in rows if r['kind']=='Продажи'),D(20))
        self.assertEqual(sum(r['total'] for r in rows if r['kind']=='Директора'),D(70))

    def test_signed_rates_and_returns_clip_potential_before_shares(self):
        data=list(inputs());data[0]['branches']['blocks'][0]['entries'][0]['rates']=['-.1']*5
        rows,_=at_full_plan([row(amount='-100')],RateBook(*data),2027,D(80),D(20))
        self.assertEqual(next(r['total'] for r in rows if r['kind']=='Политики'),D(10))
        self.assertEqual(next(r['total'] for r in rows if r['kind']=='Продажи'),D(0))

    def test_2025_tt_rates_equal_moscow_and_recipients_are_historical(self):
        book=self.book()
        self.assertEqual(book.get(TT,'Политики',('1. RAC','Бытовые кондиционеры','DAIKIN'),2025)[0],(D('.1'),)*5)
        self.assertEqual(list(book.directors_for(TT,2025)),[('directors_0_0',3)])
        self.assertEqual(list(book.directors_for(MOSCOW,2025)),[('directors_1_0',4)])
        records=book.coefficients_2025([MOSCOW,TT])
        self.assertEqual(len(records),8)
        self.assertEqual(records[0]['planning'],'1. RAC')
        self.assertEqual({r['subdivision'] for r in records},{MOSCOW,TT})

    def test_missing_historical_mapping_is_not_replaced_by_current_employee(self):
        data=list(inputs());data[1]['directors_0_0']['employee_2025']=None
        with self.assertRaisesMessage(ValidationError,'2025'):RateBook(*data)

    def test_fallback_does_not_borrow_another_branchs_rates(self):
        book=self.book()
        values,origin=book.get(MOSCOW,'Политики',('Unknown','*','*'),2027)
        self.assertEqual(values,(D('.1'),)*5);self.assertEqual(origin,'Минимум таблицы')
        with self.assertRaises(ValidationError):book.get('Other','Продажи',('1. RAC','*','*'),2027)

    def test_duplicate_merged_director_is_not_double_counted(self):
        data=list(inputs());data[1]['directors_1_0']['employee']=1;data[1]['directors_1_0']['subdivisions']=[2]
        book=RateBook(*data)
        with self.assertRaisesMessage(ValidationError,'разные ставки'):list(book.directors_for(MERGED,2027))
        data[0]['directors']['blocks'][1]['entries']=deepcopy(data[0]['directors']['blocks'][0]['entries'])
        self.assertEqual(len(list(RateBook(*data).directors_for(MERGED,2027))),1)

    def test_summary_has_kind_totals_branch_subtotals_and_exact_grand_total(self):
        rows,_=at_full_plan([row()],self.book(),2027,D(80),D(20))
        summary=aggregate_rows(rows)
        total=next(r for r in summary if r['branch']=='ИТОГО')
        self.assertEqual(total['total'],D(100));self.assertEqual(total['annual_accrued'],D(20))
        self.assertEqual(len([r for r in summary if r['branch']=='Все выбранные подразделения']),3)
        self.assertTrue(any(r['kind']=='ИТОГО по подразделению' for r in summary))

    def test_operation_lock_rejects_overlapping_worker_and_releases(self):
        from .services.coefficient_iteration_lock import operation
        with operation(987654321):
            with self.assertRaises(ValidationError):
                with operation(987654321):pass
        with operation(987654321):pass


class NativeSQLTests(SimpleTestCase):
    def test_native_engine_is_scoped_and_writes_only_temporary_outputs(self):
        from .services.coefficient_iteration_2025 import build_sql
        sql=build_sql()
        self.assertEqual(sql.count('?'),5)
        self.assertNotIn('CREATE PROCEDURE',sql)
        self.assertNotIn('DataWH.planning.PlanSales',sql)
        self.assertNotIn('DataWH.motivation.FactTest2025',sql)
        self.assertIn('INTO #iteration_fact',sql)
        self.assertIn('INTO #iteration_report',sql)
        self.assertIn('FROM #iteration_plan t',sql)
        self.assertIn('GROUP BY Subdivision,PGSales,Group_,Brand,TypeCoeff',sql)
        self.assertIn('GROUP BY r.Subdivision,r.PGSales,r.TypeCoeff',sql)
        self.assertEqual(sql.count('tm.Subdivision='),4)
        self.assertIn('gm.Subdivision=sub.SubdivisionName',sql)
        self.assertIn('gm.Subdivision=s.SubdivisionName',sql)
        self.assertIn('PortalOriginalDealAuthor',sql)
        self.assertIn('decimal(20,18)',sql)
        self.assertIn('FROM #iteration_defaults',sql)
        self.assertIn('SELECT CONVERT(nvarchar(36),@PortalPreparedRun) AS prepared_run_id',sql)
        self.assertIn('PortalPreparedSales',sql)
        self.assertIn('MIN(pick.AllocationLevel)',sql)
        self.assertIn('THROW 50040',sql)

    def test_xml_does_not_interpolate_sql(self):
        from .services.coefficient_iteration_2025 import xml
        payload=xml([{'subdivision':"A' & <B>",'planning':'x'}])
        self.assertIn('&amp;',payload);self.assertIn('&lt;B&gt;',payload)


class CalculationTests(TestCase):
    def setUp(self):
        self.user=authorize_test_case(self)
        channel=Chanel.objects.create(chanel_name='iteration-test')
        self.subs=[]
        for name in (MOSCOW,TT):self.subs.append(Subdivision.all_objects.create(subdivision_key=name,subdivision_name=name,subdivision_global=name,chanel=channel))
        self.people=[DirectorEmployee.objects.create(full_name=name,employee_code='it-'+str(i)) for i,name in enumerate(('Волков','Галайда','Кудицкая','Чевокин'))]
        payload,mappings,_,_=inputs()
        for value in mappings.values():
            if 'subdivision' in value:value['subdivision']=self.subs[value['subdivision']-1].pk
            else:
                value['employee']=self.people[value['employee']-1].pk;value['employee_2025']=self.people[value['employee_2025']-1].pk
                value['subdivisions']=[self.subs[i-1].pk for i in value['subdivisions']]
        self.iteration=CoefficientIteration.objects.create(owner=self.user,payload=payload,mappings=mappings,confirmed=True)
        self.plans={}
        for year in (2025,2026,2027):
            plan=SalesPlanScenario.objects.create(title=f'Plan {year}',source_version=f'P{year}',loaded_at=timezone.now(),source_type='erp')
            for sub in self.subs:
                SalesPlanLine.objects.create(scenario=plan,plan_date=date(year,10,1),subdivision=sub.subdivision_key,
                    planning_group_sales='1. RAC',group='Бытовые кондиционеры',brand='DAIKIN',amount_usd=100,segment_amounts={'O0':'0','O1':'100','O2':'0','O3':'0','O4':'0'},
                    source_subdivisions={sub.subdivision_key:{'amount':'100','segments':{'O0':'0','O1':'100','O2':'0','O3':'0','O4':'0'}}})
            self.plans[year]=plan
        self.version=GlobalCoeffVersion.objects.get(effective_from=date(2001,1,1));self.version.status='approved'
        from django.db.models import QuerySet
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        self.config={'scenarios':['moscow_q4_2026','full_2027'],'plan_2025':None,'plan_2026':self.plans[2026].pk,'plan_2027':self.plans[2027].pk,
                     'formula_version':self.version.pk,'quarter_share':'80','year_share':'20','payout_threshold':'.8'}
        self.url=reverse('motivation_coefficient_iteration_calculations',args=[self.iteration.pk])

    def make(self):return start(self.iteration.pk,self.user,self.config,self.iteration.revision)

    def test_synthetic_runs_do_not_connect_to_dwh_or_modify_live_data(self):
        before=list(SalesPlanLine.objects.values());maps=deepcopy(self.iteration.mappings)
        run=self.make();connector=Mock(side_effect=AssertionError('No DWH for plan scenarios'))
        execute(run,Mock(),connector=connector)
        self.assertEqual(run.state,'completed');connector.assert_not_called()
        self.assertEqual(set(run.results),{'moscow_q4_2026','full_2027'})
        self.assertEqual(run.inputs['plans']['2027']['source'],'erp')
        self.assertEqual(list(SalesPlanLine.objects.values()),before)
        self.iteration.refresh_from_db();self.assertEqual(self.iteration.mappings,maps)
        self.assertEqual(D(run.results['full_2027']['rows'][0]['total']),D(20))

    def test_snapshots_survive_mapping_and_plan_changes(self):
        run=self.make();self.iteration.mappings={};self.iteration.confirmed=False;self.iteration.save()
        execute(run,Mock(),connector=Mock(side_effect=AssertionError()))
        self.assertEqual(run.state,'completed')
        before=deepcopy(run.results)
        SalesPlanLine.objects.filter(scenario=self.plans[2027]).update(amount_usd=999)
        run.refresh_from_db();self.assertEqual(run.results,before)

    def test_stale_or_unconfirmed_mapping_is_rejected(self):
        with self.assertRaises(ValidationError):start(self.iteration.pk,self.user,self.config,999)
        self.iteration.confirmed=False;self.iteration.save()
        with self.assertRaises(ValidationError):self.make()

    def test_duplicate_run_is_blocked_and_proven_abandoned_worker_can_be_recovered(self):
        first=self.make()
        with self.assertRaises(ValidationError):self.make()
        second=start(self.iteration.pk,self.user,self.config,self.iteration.revision,recover=True)
        first.refresh_from_db();self.assertEqual(first.state,'failed');self.assertNotEqual(second.pk,first.pk)

    def test_partial_failure_keeps_completed_scenarios_and_previous_runs(self):
        run=self.make();execute(run,Mock());prior=deepcopy(run.results)
        self.config['scenarios'].append('actual_2025');self.config['plan_2025']=self.plans[2025].pk
        another=self.make()
        with patch('admin_motivation.services.coefficient_iteration_2025.read_2025',side_effect=ValidationError('Нет готового факта')):
            connection=Mock();execute(another,Mock(),connector=Mock(return_value=(connection,Mock())))
        self.assertEqual(another.state,'failed');self.assertEqual(another.results['full_2027']['state'],'completed')
        self.assertIn('Нет готового факта',another.results['actual_2025']['error'])
        run.refresh_from_db();self.assertEqual(run.results,prior)

    def test_form_rejects_wrong_year_and_requires_only_selected_plans(self):
        from .coefficient_iteration_run_views import CalculationForm
        data={**self.config,'revision':self.iteration.revision,'scenarios':['full_2027'],'plan_2027':self.plans[2025].pk}
        form=CalculationForm(self.iteration,data);self.assertFalse(form.is_valid())
        data['plan_2027']=self.plans[2027].pk;data['formula_version']=''
        form=CalculationForm(self.iteration,data);self.assertTrue(form.is_valid(),form.errors)
        SalesPlanLine.objects.filter(scenario=self.plans[2027]).update(plan_date=date(2026,1,1))
        with self.assertRaisesMessage(ValidationError,'другой год'):plan_rows(self.plans[2027],2027)

    def test_page_history_export_and_other_owner_are_isolated(self):
        run=self.make();execute(run,Mock())
        response=self.client.get(self.url,{'run':run.pk});self.assertEqual(response.status_code,200)
        self.assertContains(response,'Скачать результаты Excel');self.assertContains(response,'Волков')
        exported=self.client.get(self.url,{'run':run.pk,'export':'xlsx'})
        book=load_workbook(BytesIO(b''.join(exported.streaming_content)))
        self.assertIn('Сводка 2027',book.sheetnames);self.assertIn('План-факт 2027',book.sheetnames)
        sheet=book['Сводка 2027'];self.assertTrue(sheet.column_dimensions['D'].hidden)
        self.assertTrue(sheet.column_dimensions['E'].collapsed);self.assertFalse(sheet.sheet_view.showGridLines)
        self.assertEqual(sheet['K2'].value,16)
        self.assertEqual(sheet['D1'].value,'1 кв.: премия без условия выплаты, USD')
        book.close();exported.close()
        other=User.objects.create_user('other-editor',is_superuser=True,is_staff=True);self.client.force_login(other)
        self.assertEqual(self.client.get(self.url,{'run':run.pk}).status_code,404)
        self.assertEqual(self.client.get(self.url,{'run':run.pk,'export':'xlsx'}).status_code,404)

    def test_async_post_returns_result_url_and_uses_one_nonce(self):
        from .services.director_scenario_progress import token_for
        data={**{k:v for k,v in self.config.items() if v is not None},'revision':self.iteration.revision,'progress_token':token_for(self.user)}
        response=self.client.post(self.url,data,HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertIn('?run=',response.json()['url'])
        count=CoefficientIterationRun.objects.count()
        second=self.client.post(self.url,data,HTTP_ACCEPT='application/json')
        self.assertEqual(second.status_code,400);self.assertEqual(CoefficientIterationRun.objects.count(),count)


class NativeResultTests(SimpleTestCase):
    def test_native_outputs_keep_paid_possible_annual_and_bonus_distinct(self):
        from .services.coefficient_iteration_2025 import read_2025
        cursor=Mock();cursor.nextset.return_value=False
        cursor.description=[('prepared_run_id',)]
        cursor.fetchone.side_effect=[(0,),(1,2,3,None),('used-run',),('used-run','2026-11-12')]
        cursor.fetchall.side_effect=[[],[],[
            ('Политики',MOSCOW,'Author','1 Квартал',D(8),D(0),D(8)),
            ('Политики',MOSCOW,'Author','Год',D(2),D(1),D(2))],
            [('Политики',MOSCOW,'Author',1,D(10))]]
        with patch('admin_motivation.services.coefficient_iteration_2025.build_sql',return_value='native SQL'):
            rows,meta=read_2025(cursor,[row(year=2025),row(year=2025,heat=True)],RateBook(*inputs()),
                {'quarter_share':'80','year_share':'20','payout_threshold':'.8','formula_defaults':[]},Mock())
        self.assertEqual(rows[0]['quarters'],[D(8),0,0,0]);self.assertEqual(rows[0]['possible'][0],D(8))
        self.assertEqual(rows[0]['annual_accrued'],D(2));self.assertEqual(rows[0]['annual_paid'],D(2))
        self.assertEqual(rows[0]['bonus'],D(1));self.assertEqual(rows[0]['total'],D(11))
        self.assertEqual(meta['prepared_run_id'],'used-run')
        inserted=next(call.args[1] for call in cursor.executemany.call_args_list if '#iteration_plan' in call.args[0])
        self.assertEqual(inserted[0][3],'Бытовые кондиционеры')
        self.assertEqual(inserted[1][3:5],('',''))
        sqls=[call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any('FROM #iteration_fact' in sql and "N'Политики'" in sql and 'HEAT' in sql for sql in sqls))
        self.assertTrue(any('r.RunId=?' in sql for sql in sqls))


class HistoricalDirectorMoneyTests(SimpleTestCase):
    def test_historical_people_receive_new_rates_and_annual_share(self):
        from .services.coefficient_iteration_runner import directors_2025
        cursor=Mock();cursor.fetchall.return_value=[(sub,1,'1. RAC','Бытовые кондиционеры','DAIKIN','O1',D(100),D(0)) for sub in (MOSCOW,TT)]
        snapshot={'directors':[],'corrections':[],'aliases':{},'default':'Подразделение(Глобально)'}
        rows,_=directors_2025(cursor,[row(year=2025),row(TT,2025)],RateBook(*inputs()),
            {'quarter_share':'80','year_share':'20','payout_threshold':'.8'},snapshot)
        actual={r['employee']:(r['branch'],r['quarters'][0],r['annual_paid'],r['total']) for r in rows}
        self.assertEqual(actual,{'Кудицкая':(TT,D(24),D(6),D(30)),'Чевокин':(MOSCOW,D(32),D(8),D(40))})

    def test_annual_accrual_is_nonzero_even_when_quarter_and_year_conditions_fail(self):
        from .services.coefficient_iteration_runner import directors_2025
        cursor=Mock();cursor.fetchall.return_value=[(sub,2,'1. RAC','Бытовые кондиционеры','DAIKIN','O1',D(50),D(0)) for sub in (MOSCOW,TT)]
        snapshot={'directors':[],'corrections':[],'aliases':{},'default':'Подразделение(Глобально)'}
        rows,_=directors_2025(cursor,[row(year=2025,month=4),row(TT,2025,4)],RateBook(*inputs()),
            {'quarter_share':'80','year_share':'20','payout_threshold':'.8'},snapshot)
        for result in rows:
            self.assertEqual(sum(result['quarters']),0);self.assertEqual(result['annual_paid'],0)
            self.assertGreater(result['annual_accrued'],0);self.assertGreater(result['annual_quarters'][1],0)
            self.assertEqual(result['total'],0)
