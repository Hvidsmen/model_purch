from decimal import Decimal as D
from types import SimpleNamespace
from copy import deepcopy
from django.test import SimpleTestCase,TestCase
from django.http import QueryDict
from django.template.loader import render_to_string
from .services.zero_premium_reasons import Reasons


def source(quarter,fact):
    return {'Подразделение':'Д-Питер','АвторСделки':'Иванов','Год':2026,'КварталМотивации':str(quarter)+' Квартал',
            'ГруппаПлановПродаж':'RAC','ГруппаТоваров':'G','Марка':'B','VariationCalculate':'Группа планов продаж',
            'СуммаПланUSDКвартал':100,'СуммаОтгрузокUSDКвартал':fact,
            'СуммаПланUSD':100,'СуммаОтгрузокUSD':93,'СуммаМотивацииUSD':40,'СуммаМотивацииUSDУчетПроцента':0}


def base(fact):
    return dict(subdivision='Д-Питер',year=2026,period='2 квартал',plan=D(100),fact=D(fact))


def run():
    return SimpleNamespace(configuration={'payout_threshold':'.8','rule':'shareholder-original-2026-v2'},
        metadata={'regional_rule':'rostov-piter-policy-plans-2026-v1','regional_gate_version':'four-checks-v1'})


class ZeroPremiumReasonTests(SimpleTestCase):
    def test_reports_failed_policy_gate_but_not_passed_department_personal_or_formula(self):
        raw=[source(1,90),source(2,96)];before=deepcopy(raw)
        reasons=Reasons(run(),'Политики',raw,[raw[1]],[base('86.76')],[base('71.56')]).get('Д-Питер','Иванов',2026,'2',D(0))
        self.assertEqual(len(reasons),1)
        self.assertIn('порог политиков: 71,56% при требуемых 80%',reasons[0])
        self.assertEqual(raw,before)

    def test_all_failed_gates_are_listed(self):
        raw=[source(1,60),source(2,60)];raw[1]['СуммаОтгрузокUSD']=50
        reasons=Reasons(run(),'Политики',raw,[raw[1]],[base(70)],[base(71)]).get('Д-Питер','Иванов',2026,'2',D(0))
        self.assertEqual(len(reasons),4)
        text=' '.join(reasons)
        for label in ('порог подразделения','порог политиков','порог личного плана','порог формулы'):self.assertIn(label,text)

    def test_no_false_failed_gate_for_no_premium_or_closing_multiplier(self):
        raw=[source(1,90),source(2,96)]
        raw[1]['СуммаМотивацииUSD']=0
        reasons=Reasons(run(),'Политики',raw,[raw[1]],[base(90)],[base(90)]).get('Д-Питер','Иванов',2026,'2',D(0))
        self.assertIn('нет расчётной премии',reasons[0])
        raw[1]['СуммаМотивацииUSD']=40;raw[1]['СуммаМотивацииUSDУчетПроцента']=40
        reasons=Reasons(run(),'Политики',raw,[raw[1]],[base(90)],[base(90)]).get('Д-Питер','Иванов',2026,'2',D(0))
        self.assertIn('поправка руководителя',reasons[0])
        self.assertNotIn('не пройден',reasons[0])

    def test_repeated_formula_windows_with_different_row_weights_keep_exact_gate(self):
        raw=[source(1,90),source(2,96)]
        raw[1]['СуммаОтгрузокUSD']=70
        repeated={**raw[1],'СуммаПланUSD':200,'СуммаОтгрузокUSD':140}
        reasons=Reasons(run(),'Политики',raw,[raw[1],repeated],[base(90)],[base(90)]).get('Д-Питер','Иванов',2026,'2',D(0))
        self.assertEqual(len(reasons),1)
        self.assertIn('порог формулы',reasons[0]);self.assertIn('70%',reasons[0])

    def test_separate_moscow_windows_do_not_prove_combined_annual_type_failure(self):
        calculation=run();calculation.scenario='separate'
        annual={**source(2,90),'Подразделение':'Д-Москва','КварталМотивации':'Год'}
        kind_base={**base(10),'subdivision':'Д-Москва','period':'Год'}
        reasons=Reasons(calculation,'Продажи',[annual],[annual],[],[kind_base]).get('Д-Москва','Иванов',2026,'5',D(0))
        self.assertNotIn('не пройден порог',' '.join(reasons))
        self.assertIn('недостаточно данных',reasons[0])

    def test_first_quarter_is_not_described_as_failed_plan_gate(self):
        raw=[source(1,10)]
        reasons=Reasons(run(),'Политики',raw,raw,[base(10)],[base(10)]).get('Д-Питер','Иванов',2026,'1',D(0))
        self.assertIn('без проверки плана',reasons[0])


class ZeroPremiumReportTests(TestCase):
    def test_visible_period_and_total_footnotes_use_frozen_failed_checks(self):
        from .test_shareholder_original_rules import OriginalSnapshotTests,gate
        from .test_shareholder_personal_targets import source_rows
        from .services import shareholder_reports,shareholder_runner
        from .services.reports_next_presenter import present
        from .models import ShareholderSnapshot
        OriginalSnapshotTests.setUp(self)
        self.run.chunks.all().delete()
        self.run.metadata.update(run().metadata)
        rows=source_rows()
        for row in rows:
            if row['КварталМотивации']=='2 Квартал':row['СуммаМотивацииUSDУчетПроцента']=0
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='report_pol',sequence=0,payload=shareholder_runner.serial(rows))
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='eligibility_pol',sequence=0,payload=[gate(r,False) for r in rows if r['КварталМотивации']=='Год'])
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='dept_pol',sequence=0,payload=[{'Подразделение':'Д-Питер','Год':2026,'КварталМотивации':'2 Квартал','Plan_':100,'Fact_':90}])
        query=QueryDict('year=2026&period=2&type=Политики&subdivision=Д-Питер&author=Иванов')
        data=shareholder_reports.data_for(self.run,query,detailed=False)
        display=present('mine',data,data,['2'])
        self.assertEqual(display['total'],0)
        self.assertTrue(display['period_cards'][0]['zero_reasons'])
        self.assertTrue(display['zero_reasons'])
        html=render_to_string('admin_motivation/reports_next/_my_periods.html',{'presentation':display,'ledger':data,'shareholder_report':True})
        self.assertIn('порог политиков: 50% при требуемых 80%',html)
        self.assertIn('порог личного плана',html)
        self.assertNotIn('не пройден порог подразделения',html)
        self.assertIn('role="note"',html)
