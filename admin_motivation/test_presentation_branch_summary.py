from decimal import Decimal as D
from django.http import QueryDict
from django.test import SimpleTestCase
from openpyxl import load_workbook
from .services.presentation_excel import workbook
from .test_quarter_totals import premium


class BranchSummaryExcelTests(SimpleTestCase):
    def ledger(self,currency='USD',unknown=False):
        rows=[]
        for branch,multiplier in [('Д-Москва',1),('Термотрейд',2)]:
            for kind,rate in [('Политики',1),('Продажи',2),('Директора',3)]:
                for quarter,value in [('1',10),('2',20)]:
                    item=premium(quarter,kind,str(value*rate*multiplier),branch=branch)
                    item['currency']=currency
                    if branch=='Д-Москва' and kind=='Политики' and quarter=='2':
                        item['bonus']=D(5);item['total']+=5
                    if unknown and branch=='Термотрейд' and kind=='Продажи':item['possible']=None
                    rows.append(item)
        return {'rows':rows,'total':sum(item['total'] for item in rows),'currency':currency,
            'kinds':['Политики','Продажи','Директора']}

    def test_each_branch_has_kind_rows_subtotal_and_reconciled_grand_total(self):
        for currency in ('USD','RUB'):
            with self.subTest(currency=currency):
                output=workbook('team',self.ledger(currency),[],QueryDict('period=1&period=2'),title='Все кварталы 2026')
                self.addCleanup(output.close)
                book=load_workbook(output);self.addCleanup(book.close)
                sheet=book['Сводка'];rows=list(sheet.values)
                self.assertEqual(rows[4][:3],('Подразделение','Вид мотивации','Сотрудников'))
                self.assertEqual(rows[4][3:5],('Премия за 1 кв.','Премия за 2 кв.'))
                self.assertEqual(sheet.freeze_panes,'D6')
                for branch,expected in [('Д-Москва',(60,120,5,185)),('Термотрейд',(120,240,0,360))]:
                    items=[row for row in rows if row[0]==branch and row[1] in self.ledger()['kinds']]
                    self.assertEqual(len(items),3)
                    subtotal=next(row for row in rows if row[:2]==(branch,'ИТОГО по подразделению'))
                    self.assertEqual(subtotal[2],1) # Same author across motivation kinds.
                    self.assertEqual(subtotal[3:7],expected)
                    self.assertEqual(sum(row[6] for row in items),subtotal[6])
                combined=[row for row in rows if row[0]=='Все выбранные подразделения']
                self.assertEqual([row[1] for row in combined],['Политики','Продажи','Директора'])
                self.assertEqual([row[6] for row in combined],[95,180,270])
                self.assertEqual([row[2] for row in combined],[2,2,2])
                grand=next(row for row in rows if row[0]=='ИТОГО')
                self.assertEqual(sum(row[6] for row in combined),grand[6])
                self.assertEqual(grand[2:7],(2,180,360,5,545))
                self.assertEqual(grand[-1],540)

    def test_missing_possible_stays_unknown_in_branch_and_grand_totals(self):
        output=workbook('team',self.ledger(unknown=True),[],QueryDict('period=1&period=2'))
        self.addCleanup(output.close)
        book=load_workbook(output);self.addCleanup(book.close)
        rows=list(book['Сводка'].values)
        self.assertIsNone(next(row for row in rows if row[:2]==('Термотрейд','ИТОГО по подразделению'))[-1])
        self.assertIsNone(next(row for row in rows if row[0]=='ИТОГО')[-1])
        self.assertEqual(next(row for row in rows if row[0]=='ИТОГО')[-2],545)

    def test_annual_contributions_reconcile_by_branch_kind_and_grand_without_payout_change(self):
        ledger=self.ledger()
        for item in ledger['rows']:item['annual_accrual']=item['amount']/4
        # A displayed year is payout, not another quarter contribution.
        year=premium('5','Политики','0',branch='Д-Москва')
        year.update(currency='USD',annual_accrual=D(999));ledger['rows'].append(year)
        output=workbook('team',ledger,[],QueryDict('period=1&period=2&period=5'))
        self.addCleanup(output.close)
        book=load_workbook(output);self.addCleanup(book.close)
        rows=list(book['Сводка'].values);headers=list(rows[4]);column=headers.index('В годовую премию, USD')
        get=lambda branch,kind:next(row for row in rows if row[:2]==(branch,kind))
        self.assertEqual(get('Д-Москва','ИТОГО по подразделению')[column],45)
        self.assertEqual(get('Термотрейд','ИТОГО по подразделению')[column],90)
        self.assertEqual([get('Все выбранные подразделения',kind)[column] for kind in ledger['kinds']],[22.5,45,67.5])
        grand=next(row for row in rows if row[0]=='ИТОГО')
        self.assertEqual(grand[column],135)
        self.assertEqual(grand[headers.index('Всего по расчёту')],545)

    def test_missing_annual_data_is_blank_not_estimated_from_quarter_payout(self):
        ledger=self.ledger()
        for item in ledger['rows']:item['annual_accrual']=D(1)
        ledger['rows'][0]['annual_accrual']=None
        output=workbook('team',ledger,[],QueryDict('period=1&period=2'))
        self.addCleanup(output.close)
        book=load_workbook(output);self.addCleanup(book.close)
        rows=list(book['Сводка'].values);column=list(rows[4]).index('В годовую премию, USD')
        self.assertIsNone(next(row for row in rows if row[:2]==('Д-Москва','Политики'))[column])
        self.assertIsNone(next(row for row in rows if row[0]=='ИТОГО')[column])
