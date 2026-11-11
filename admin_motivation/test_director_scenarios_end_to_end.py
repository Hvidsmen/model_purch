from datetime import date
from decimal import Decimal as D
from io import BytesIO
import json
import os
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase
from django.db.models import QuerySet
from django.urls import reverse
from openpyxl import load_workbook
from ._director_test_warehouse import Warehouse
from .models import (GlobalCoeffVersion,SubdivisionDirectorCoeff,DiscountSegment,
 MotivationCalculationSchedule,DirectorScenarioComparison)
from . import test_director_scenarios as scenario_helpers

GOLDEN=((4,120),(4,72),(24,48),(48,96),(48,96))


class ScenarioFlow:
    line=scenario_helpers.DirectorScenarioTests.line

    def setup_flow(self):
        scenario_helpers.DirectorScenarioTests.setUp(self)
        old=self.version
        self.version=GlobalCoeffVersion.objects.create(effective_from=date(2026,1,1),title='Контрольный пример · синтетические данные')
        for row in SubdivisionDirectorCoeff.objects.filter(version=old):
            row.pk=None;row.version=self.version;row.save()
        DiscountSegment.objects.create(version=self.version,type_discount='MASS',o0=0,o1=10,o2=20,o3=30,o4=40)
        MotivationCalculationSchedule.objects.create(name='Synthetic main settings',variant='main',fact_year=2026,
            plan_version='P2026',quarter_share=80,year_share=20,payout_threshold='.8')
        for person in self.people[2:]:person.is_archived=True;person.save(update_fields=['is_archived'])
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        self.warehouse=Warehouse();self.addCleanup(self.warehouse.close)
        self.url=reverse('motivation_report_director_scenarios')
        self.payload={'plan_2025':self.plans[2025].pk,'plan_2026':self.plans[2026].pk,'coefficient_version':self.version.pk,
            **dict(zip(('source_moscow','source_tt','target_moscow','target_tt'),[p.pk for p in self.people]))}

    def submit(self):
        # Only the connection boundary is replaced, not read_sources, rates,
        # financial SELECTs, calculate, persistence, views or Excel generation.
        with patch('admin_motivation.services.director_scenario_inputs.connect_database',self.warehouse.connector):
            return self.client.post(self.url,self.payload)

    def book(self,url):
        response=self.client.get(url+'&export=xlsx')
        raw=b''.join(response.streaming_content)
        return load_workbook(BytesIO(raw)),raw


class DirectorEndToEndTests(ScenarioFlow,TestCase):
    def setUp(self):self.setup_flow()

    def test_source_reads_all_five_cases_snapshot_reload_and_every_excel_sheet(self):
        roster=list(self.people[0].subdivisions.through.objects.values_list('directoremployee_id','subdivision_id'))
        rates=list(SubdivisionDirectorCoeff.objects.values_list('pk','employee_id','k0','k1','k2','k3','k4'))
        response=self.submit();self.assertEqual(response.status_code,302)
        saved=DirectorScenarioComparison.objects.get()
        self.assertEqual(len(saved.results['rows']),10)
        self.assertEqual(saved.results['sources']['2025']['run_id'],self.warehouse.source_run)
        self.assertEqual(saved.results['sources']['2026']['run_id'],self.warehouse.prepared_run)
        for case,totals in enumerate(GOLDEN,1):
            rows=[row for row in saved.results['rows'] if row['scenario']==case]
            self.assertEqual([D(row['total']) for row in rows],list(map(D,totals)))
            self.assertEqual([row['employee_id'] for row in rows],[p.pk for p in (self.people[2:] if case==1 else self.people[:2])])
            for row in rows:
                detail=[d for d in saved.results['details'] if d['scenario']==case and d['employee']==row['employee']]
                self.assertEqual(sum(D(d['paid'])+D(d['bonus']) for d in detail),D(row['total']))
                if case>1:
                    self.assertEqual(D(row['annual_paid']),0);self.assertEqual(D(row['quarters'][3]),0)
        calls=len(self.warehouse.calls)
        self.assertEqual(self.client.get(response.url).status_code,200)
        book,raw=self.book(response.url)
        self.assertEqual(len(self.warehouse.calls),calls)
        self.assertEqual(book.sheetnames,['Сравнение',*[f'{i} Вариант' for i in range(1,6)],'Выполнение','Ставки','Параметры'])
        self.assertEqual(book['Сравнение'].max_row,11)
        for case,totals in enumerate(GOLDEN,1):
            sheet=book[f'{case} Вариант'];self.assertEqual(sheet.max_row,3)
            for row,value in enumerate(totals,2):
                self.assertEqual(sheet.cell(row,14).value,value)
                self.assertEqual(sheet.cell(row,14).data_type,'n')
            if case>1:self.assertIsNone(sheet['J2'].value)
        self.assertEqual(book['Выполнение']['L2'].number_format,'0.00%')
        self.assertEqual(book['Ставки']['F2'].number_format,'0.00%')
        metadata=str(list(book['Параметры'].values))
        self.assertIn(self.warehouse.source_run,metadata);self.assertIn(self.warehouse.prepared_run,metadata)
        self.assertIn('settings_job_key',metadata)
        self.assertEqual(roster,list(self.people[0].subdivisions.through.objects.values_list('directoremployee_id','subdivision_id')))
        self.assertEqual(rates,list(SubdivisionDirectorCoeff.objects.values_list('pk','employee_id','k0','k1','k2','k3','k4')))
        self.assertTrue(all(c.closed for c in self.warehouse.connections))
        if path:=os.environ.get('DIRECTOR_VERIFICATION_OUTPUT'):
            Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).write_bytes(raw)
            Path(path).with_suffix('.json').write_text(json.dumps({'synthetic':True,'golden_totals':GOLDEN,
                'actual_totals':[[r['total'] for r in saved.results['rows'] if r['scenario']==case] for case in range(1,6)],
                'sheets':book.sheetnames,'sql_metadata_and_locks':'simulated; financial SELECTs executed in SQLite'},ensure_ascii=False,indent=2))

    def test_later_sources_cannot_change_saved_excel_and_new_run_gets_new_source_values(self):
        response=self.submit();first=DirectorScenarioComparison.objects.get();original=first.results
        self.warehouse.change("UPDATE PortalPreparedSales SET Price=Price+100 WHERE Subdivision='M' AND ClosedDealDate<'20261001'")
        self.submit();self.assertEqual(DirectorScenarioComparison.objects.count(),2)
        first.refresh_from_db();self.assertEqual(first.results,original)
        book,raw=self.book(response.url);self.assertEqual(book['2 Вариант']['N2'].value,4)
        newest=DirectorScenarioComparison.objects.first()
        self.assertEqual(D(next(r['total'] for r in newest.results['rows'] if r['scenario']==2 and r['employee_id']==self.people[0].pk)),D('4.8'))

    def test_late_failure_keeps_previous_snapshot_and_returns_actionable_error(self):
        self.submit();original=DirectorScenarioComparison.objects.get().results
        self.warehouse.change("UPDATE PreparedReady SET SourceSchema='outdated'")
        response=self.submit()
        self.assertContains(response,'совместимого готового набора')
        self.assertEqual(DirectorScenarioComparison.objects.count(),1)
        self.assertEqual(DirectorScenarioComparison.objects.get().results,original)
        self.assertTrue(all(c.closed for c in self.warehouse.connections))

    def test_failed_2025_and_busy_source_do_not_create_partial_comparison(self):
        self.warehouse.change("UPDATE RunJournal SET State_='failed'")
        response=self.submit();self.assertContains(response,'завершённого')
        self.assertEqual(DirectorScenarioComparison.objects.count(),0)
        self.warehouse.lock_result=-1
        response=self.submit();self.assertContains(response,'выполняется исходный расчёт')
        self.assertEqual(DirectorScenarioComparison.objects.count(),0)

    def test_ambiguous_segments_stop_before_any_result_is_saved(self):
        # Mimic model edit while retaining the selected approved revision in test.
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='draft')
        DiscountSegment.objects.create(version=self.version,type_discount='ASS',o1=5,o2=15,o3=25,o4=35)
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        self.warehouse.change("UPDATE PortalPreparedSales SET ДаичиТипСкидкиНаДату='K_MASS'")
        response=self.submit();self.assertContains(response,'несколько типов скидки')
        self.assertEqual(DirectorScenarioComparison.objects.count(),0)

    def test_mobile_classification_is_financially_consistent_in_all_five_cases(self):
        from .models import Goods,SalesPlanLine
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='draft')
        good=Goods.objects.create(goods_key='Mobile',planning_group_sales='1. RAC',group='Мобильные кондиционеры',brand='*')
        formula=SubdivisionDirectorCoeff.objects.filter(version=self.version,employee=self.people[0]).first().variation_calculate
        for index,person in enumerate(self.people[:2]):
            SubdivisionDirectorCoeff.objects.create(version=self.version,employee=person,subdivision=self.branches[index],goods=good,
                variation_calculate=formula,**{f'k{i}':'.03' if index==0 else '.04' for i in range(5)})
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        for year,plan in self.plans.items():
            for branch in self.branches:
                for q in range(1,5):SalesPlanLine.objects.create(scenario=plan,subdivision=branch.subdivision_key,
                    plan_date=date(year,q*3-2,1),planning_group_sales='1. RAC',group='Мобильные кондиционеры',
                    brand='Manufacturer',amount_usd=100,segment_amounts={'O0':'100',**{f'O{i}':'0' for i in range(1,5)}})
        db=self.warehouse.open()
        for key,branch in [('M',self.branches[0].subdivision_key),('T',self.branches[1].subdivision_key)]:
            for q in range(1,5):db.execute('INSERT INTO FactTest2025 VALUES (?,?,2025,?,?,?,?,?,?,?)',
                (branch,key,q,'1. RAC','Мобильные кондиционеры','','O0',100,'Продажи'))
            for month in (1,4,7):self.warehouse.sale(db,key,100,day=f'2026{month:02d}15',doc=f'Mobile-{key}-{month}',product='Mobile')
        db.commit();db.close()
        response=self.submit();self.assertEqual(response.status_code,302)
        data=DirectorScenarioComparison.objects.get().results
        expected=[('6.4','136'),('6.4','81.6'),('31.2','57.6'),('62.4','115.2'),('62.4','115.2')]
        for case,values in enumerate(expected,1):
            self.assertEqual([D(r['total']) for r in data['rows'] if r['scenario']==case],list(map(D,values)))

    def test_no_actual_sales_for_one_branch_does_not_reuse_the_other_branch(self):
        self.warehouse.change("DELETE FROM PortalPreparedSales WHERE Subdivision='M'")
        response=self.submit();self.assertEqual(response.status_code,302)
        rows=DirectorScenarioComparison.objects.get().results['rows']
        self.assertEqual([D(r['total']) for r in rows if r['scenario']==2],[0,72])
        self.assertEqual([D(r['total']) for r in rows if r['scenario']==4],[12,24])
        self.assertEqual([D(r['total']) for r in rows if r['scenario']==3],[24,48])

    def test_negative_rate_only_accrues_positive_premium_on_returns(self):
        QuerySet(model=SubdivisionDirectorCoeff).filter(version=self.version,employee=self.people[0]).update(**{f'k{i}':-.01 for i in range(5)})
        db=self.warehouse.open();self.warehouse.sale(db,'M',-100,day='20260715',doc='Return');db.commit();db.close()
        response=self.submit();self.assertEqual(response.status_code,302)
        data=DirectorScenarioComparison.objects.get().results
        self.assertEqual(D(next(r['total'] for r in data['rows'] if r['scenario']==4 and r['employee_id']==self.people[0].pk)),D('.8'))
        self.assertEqual(D(next(r['total'] for r in data['rows'] if r['scenario']==3 and r['employee_id']==self.people[0].pk)),0)

    def test_old_2025_rule_and_missing_sql_column_are_rejected_with_no_partial_snapshot(self):
        from .services.recalculation_2025 import RULE_VERSION
        params={'fact_year':2025,'plan_version':'P2025','quarter_share':'80','year_share':'20','payout_threshold':'.8',
            'variant':'test_2025','recalculation_rule':'old-rule'}
        self.warehouse.change('UPDATE RunJournal SET Parameters_=?',(json.dumps(params),))
        self.assertContains(self.submit(),'по прежним правилам')
        self.assertEqual(DirectorScenarioComparison.objects.count(),0)
        params['recalculation_rule']=RULE_VERSION
        self.warehouse.change('UPDATE RunJournal SET Parameters_=?',(json.dumps(params),))
        self.warehouse.change('ALTER TABLE Product RENAME COLUMN BrandName TO MissingBrand')
        self.assertContains(self.submit(),'dbo.Product.BrandName')
        self.assertEqual(DirectorScenarioComparison.objects.count(),0)
        self.assertTrue(all(c.closed for c in self.warehouse.connections))

    def test_invalid_actor_rate_configuration_fails_before_warehouse_connection(self):
        QuerySet(model=SubdivisionDirectorCoeff).filter(version=self.version,employee=self.people[0]).delete()
        self.assertContains(self.submit(),'Нет директорских ставок')
        self.assertEqual(len(self.warehouse.calls),0)
        self.assertEqual(DirectorScenarioComparison.objects.count(),0)

    def test_real_discount_segments_and_synthetic_plan_segments_have_independent_bases(self):
        QuerySet(model=SubdivisionDirectorCoeff).filter(version=self.version,employee=self.people[0]).update(
            k0=.01,k1=.02,k2=.03,k3=.04,k4=.05)
        for line in self.plans[2026].lines.filter(subdivision=self.branches[0].subdivision_key):
            line.segment_amounts={'O0':'250','O1':'750','O2':'0','O3':'0','O4':'0'};line.save()
        self.warehouse.change("UPDATE PortalPreparedSales SET DiscountMotivation=20 WHERE Subdivision='M'")
        response=self.submit();self.assertEqual(response.status_code,302)
        rows=DirectorScenarioComparison.objects.get().results['rows']
        expected={2:12,3:42,4:72,5:66}
        for case,value in expected.items():
            self.assertEqual(D(next(r['total'] for r in rows if r['scenario']==case and r['employee_id']==self.people[0].pk)),value)

    def test_full_2025_annual_recovery_and_catchup_reach_excel(self):
        for quarter,amount in enumerate((100,100,100,3700),1):
            self.warehouse.change('UPDATE FactTest2025 SET СуммаОтгрузокUSD=? WHERE ПодразделениеДокумент=? AND Квартал=?',
                (amount,'M',quarter))
        response=self.submit();self.assertEqual(response.status_code,302)
        book,raw=self.book(response.url)
        self.assertEqual(book['1 Вариант']['G2'].value,.8)
        self.assertEqual(book['1 Вариант']['H2'].value,0)
        self.assertEqual(book['1 Вариант']['I2'].value,0)
        self.assertEqual(book['1 Вариант']['J2'].value,29.6)
        self.assertEqual(book['1 Вариант']['L2'].value,8)
        self.assertEqual(book['1 Вариант']['M2'].value,1.6)
        self.assertEqual(book['1 Вариант']['N2'].value,40)

    def test_both_missing_rate_sources_are_reported_in_one_preflight(self):
        QuerySet(model=SubdivisionDirectorCoeff).filter(version=self.version,employee__in=self.people[:2]).delete()
        response=self.submit()
        self.assertContains(response,self.people[0].full_name+': Нет директорских ставок')
        self.assertContains(response,self.people[1].full_name+': Нет директорских ставок')
        self.assertEqual(len(self.warehouse.calls),0)
        self.assertEqual(DirectorScenarioComparison.objects.count(),0)

    def test_exact_composite_discount_overrides_both_generic_matches(self):
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='draft')
        for name,limits in [('KF',(10,50,52,53)),('ACC',(10,36,40,42)),
                ('KF_ACC',(10,36,40,42)),('PROJECT',(None,None,None,None)),
                ('WB',(None,None,None,None)),('KF_PROJECT',(None,None,None,None)),
                ('KF_WB',(None,None,None,None)),('KF_WBC',(None,None,None,None))]:
            DiscountSegment.objects.create(version=self.version,type_discount=name,
                **dict(zip(('o1','o2','o3','o4'),limits)))
        QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        from .services.director_scenario_inputs import read_sources
        for kind,segment in [('KF_ACC','O2'),('KF_PROJECT','O0'),('KF_WB','O0'),('KF_WBC','O0')]:
            self.warehouse.change('UPDATE PortalPreparedSales SET ДаичиТипСкидкиНаДату=?,DiscountMotivation=38',(kind,))
            facts,_=read_sources(self.plans,self.version,self.warehouse.connector)
            self.assertEqual({row['segment'] for row in facts[2026]},{segment},kind)
            self.assertEqual(sum(D(row['positive']) for row in facts[2026]),6000)
