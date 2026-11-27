from copy import deepcopy
from decimal import Decimal as D
from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase,TestCase
from .services.personal_execution import rows


def base(branch='A',period='2 квартал',plan=100,fact=70):
    return dict(subdivision=branch,year=2026,period=period,plan=D(plan),fact=D(fact))


class PersonalExecutionTests(SimpleTestCase):
    def test_department_and_kind_have_distinct_bases_and_ignore_other_branches(self):
        dept=base(fact=86);kind=base(fact=71)
        ledger={'department_plan_fact':[dept,deepcopy(dept),base(branch='B',fact=2)]}
        data={'periods':['2 квартал'],'_source_rows':[{'Подразделение':'A','Год':2026}],
              'coefficient_performance':[kind,base(branch='B',fact=1)]}
        before=deepcopy((ledger,data))
        result=rows(ledger,data,'Политики','A')
        self.assertEqual([r['label'] for r in result],['% выполнения подразделения','% выполнения политиков'])
        self.assertEqual([r['metrics'][0]['ratio'] for r in result],[D('.86'),D('.71')])
        self.assertEqual([r['metrics'][0]['remaining'] for r in result],[D(14),D(29)])
        self.assertEqual((ledger,data),before)

    def test_labels_missing_bases_and_latest_cumulative_target(self):
        ledger={'department_plan_fact':[base(period='1 квартал',fact=50),base(fact=80)]}
        data={'periods':['1 квартал','2 квартал'],'_source_rows':[{'Подразделение':'A','Год':2026}],'coefficient_performance':[]}
        for kind,label in [('Продажи','продаж'),('Директора','директоров'),('Кураторы','кураторов')]:
            result=rows(ledger,data,kind,'A')
            self.assertEqual(result[1]['label'],'% выполнения '+label)
            self.assertIsNone(result[1]['metrics'][0]['ratio'])
            self.assertEqual(result[0]['total_metrics']['ratio'],D('.8'))

    def test_conflicting_duplicate_bases_are_unknown(self):
        ledger={'department_plan_fact':[base(fact=86),base(fact=85)]}
        data={'periods':['2 квартал'],'_source_rows':[{'Подразделение':'A','Год':2026}],'coefficient_performance':[]}
        self.assertIsNone(rows(ledger,data,'Политики','A')[0]['metrics'][0]['ratio'])


class PersonalExecutionReportTests(TestCase):
    def test_employee_filter_does_not_replace_department_or_kind_base(self):
        from .test_shareholder_original_rules import OriginalSnapshotTests,gate
        from .test_shareholder_personal_targets import source_rows
        from .models import ShareholderSnapshot
        from .services import shareholder_reports,shareholder_runner
        from .services.reports_next_presenter import personal_details
        OriginalSnapshotTests.setUp(self)
        self.run.chunks.all().delete()
        source=source_rows();source += [{**r,'АвторСделки':'Другой'} for r in source]
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='report_pol',sequence=0,payload=shareholder_runner.serial(source))
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='eligibility_pol',sequence=0,payload=[gate(r,True) for r in source if r['КварталМотивации']=='Год'])
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='dept_pol',sequence=0,payload=[{'Подразделение':'Д-Питер','Год':2026,'КварталМотивации':'2 Квартал','Plan_':1000,'Fact_':900}])
        query=QueryDict('year=2026&type=Политики&period=2&author=Иванов&subdivision=Д-Питер')
        data=shareholder_reports.data_for(self.run,query)
        display=personal_details(data,query,period_codes=['2'])
        group=display['product_sections'][0]['groups'][0]
        execution=group['execution_rows']
        self.assertEqual([r['metrics'][0]['ratio'] for r in execution],[D('.9'),D('.5')])
        html=render_to_string('admin_motivation/reports_next/_my_products.html',{'presentation':display,'ledger':data})
        self.assertIn('% выполнения подразделения',html)
        self.assertIn('% выполнения политиков',html)
        self.assertLess(html.index('% выполнения подразделения'),html.index('% выполнения политиков'))
        self.assertIn('90,00%',html);self.assertIn('50,00%',html)
