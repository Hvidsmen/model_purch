from io import StringIO
import json

from django.core.management import call_command
from django.test import TestCase

from . import test_shareholder_original_rules as fixtures
from .models import ShareholderSnapshot


class FormulaDiagnosticTests(TestCase):
    def test_saved_global_result_and_exact_raw_formula_are_shown_separately_without_writes(self):
        fixtures.OriginalSnapshotTests.setUp(self)
        raw={'Subdivision':'Д-Питер','PGSales':'1. RAC','Group_':'Мобильные кондиционеры',
             'Brand':'*','VariationCalculate':'Группа планов продаж'}
        normalized={**raw,'Brand':''}
        self.run.configuration['regional_rates']={'key_format':'portal-goods-v2','coefficients':[raw]}
        self.run.save(update_fields=['configuration'])
        result=fixtures.row('2',100,100,branch='Д-Питер')
        result['ГруппаТоваров']='Мобильные кондиционеры'
        for dataset,payload in [('policy_rates',[normalized]),('report_pol',[result])]:
            self.run.chunks.filter(dataset=dataset).delete()
            ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset=dataset,sequence=0,payload=payload)
        before=list(self.run.chunks.order_by('pk').values('id','payload'));configuration=self.run.configuration.copy()
        output=StringIO();call_command('diagnose_shareholder_formula',run=str(self.run.pk),stdout=output)
        data=json.loads(output.getvalue())
        self.assertEqual(data['frozen_raw_coefficients'][0]['Brand'],'*')
        self.assertEqual(data['saved_sql_coefficients'][0]['Brand'],'')
        self.assertEqual(data['frozen_raw_coefficients'][0]['VariationCalculate'],'Группа планов продаж')
        self.assertEqual(data['saved_result'][0]['formula'],'Подразделение(Глобально)')
        self.assertEqual(data['saved_result'][0]['possible_USD'],'100')
        self.run.refresh_from_db();self.assertEqual(self.run.configuration,configuration)
        self.assertEqual(list(self.run.chunks.order_by('pk').values('id','payload')),before)
