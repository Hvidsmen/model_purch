from decimal import Decimal
import sqlite3
from io import BytesIO
from unittest.mock import MagicMock
from types import SimpleNamespace
from django.test import SimpleTestCase
from openpyxl import load_workbook
from .services.recalculation_2025 import summary, workbook
from .services.recalculation_2025_performance import explain, DETAIL_SQL, gates_sql


def detail(**changes):
    row = dict(kind='Политики',branch='Branch',author='Author',planning='PG',goods='Goods',brand='Brand',
        formula='Группа планов продаж',period='2 Квартал(1-2 кв)',plan=100,fact=70,possible=10,paid=0,bonus=0)
    row.update(changes)
    return tuple(row.values())


class Performance2025Tests(SimpleTestCase):
    def test_zero_payout_explains_all_legacy_gates(self):
        rows=explain([detail()], [('Политики','Branch','Author',2,100,100,70)],'.8')
        r=rows[0]
        self.assertEqual(r['ratio'],Decimal('.7'))
        self.assertIn('по формуле ниже порога',r['reason'])
        self.assertIn('подразделения ниже порога',r['reason'])
        self.assertIn('сотрудника ниже порога',r['reason'])

    def test_global_formula_does_not_apply_personal_gate(self):
        rows=explain([detail(formula='Подразделение(Глобально)',fact=90,paid=10)],
            [('Политики','Branch','Author',2,100,101,20),('Политики','Branch','Other',2,0,1,70)],'.8')
        self.assertEqual(rows[0]['department_ratio'],Decimal('.9'))
        self.assertIsNone(rows[0]['author_ratio'])
        self.assertEqual(rows[0]['reason'],'К выплате')

    def test_no_plan_has_no_fabricated_percentage(self):
        r=explain([detail(plan=0)],[],'.8')[0]
        self.assertIsNone(r['ratio'])
        self.assertIn('нулевой или отрицательный план',r['reason'])
        self.assertIsNone(r['department_ratio'])

    def test_first_quarter_does_not_claim_threshold_blocks_payment(self):
        r=explain([detail(period='1 Квартал',plan=20,fact=20,possible=0)],[],'.8')[0]
        self.assertIsNone(r['threshold'])
        self.assertEqual(r['ratio'],1)
        self.assertIn('порог выплаты не применяется',r['reason'])
        self.assertNotIn('ниже порога',r['reason'])

    def test_supplement_is_a_payment_and_not_a_zero_premium(self):
        r=explain([detail(period='Год',possible=0,paid=0,bonus=7)],[],'.8')[0]
        self.assertEqual(r['reason'],'К выплате')
        self.assertEqual(r['total'],7)

    def test_zero_rate_does_not_get_misdiagnosed_as_failed_plan(self):
        r=explain([detail(fact=110,possible=0)],[],'.8')[0]
        self.assertIn('Начисленная премия равна нулю',r['reason'])
        self.assertNotIn('ниже порога',r['reason'])

    def test_detail_does_not_sum_repeated_windows_and_keeps_negative_values(self):
        sql=DETAIL_SQL.replace('motivation.ReportMotivationByDealAuthorTest2025','r').replace("N'","'")
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE r (ТипКоэф,Подразделение,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка,VariationCalculate,КварталМотивации,СуммаПланUSD,СуммаОтгрузокUSD,СуммаМотивацииUSD,СуммаМотивацииUSDУчетПроцента,Остаток,СуммаОтгрузокUSDКварталValue,Год)')
            base=('Политики','Branch','Author','PG','G','B','Группа планов продаж','Год')
            db.executemany('INSERT INTO r VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[
                (*base,100,70,10,0,0,30,2025),(*base,100,70,20,0,0,40,2025),(*base,0,0,0,0,5,0,2025)])
            result=db.execute(sql).fetchone()
        self.assertEqual(result[8:13],(100,70,30,0,5))

    def test_four_sheets_with_real_percent_cells_and_literal_names(self):
        sections=summary([('Политики','Branch','Author','Год',0)])
        sections[0]['performance']=explain([detail(author='=1+1')],[],'.8')
        b=load_workbook(workbook(sections))
        self.assertEqual(b.sheetnames,['1. Политики','2. Продажи','3. Выполнение политик','4. Выполнение продаж'])
        s=b.worksheets[2]
        headers={c.value:c.column for c in s[3]}
        self.assertEqual(s.cell(4,headers['Выполнение по формуле, %']).value,.7)
        self.assertEqual(s.cell(4,headers['Выполнение по формуле, %']).number_format,'0.00%')
        self.assertEqual(s.cell(4,headers['Порог выплаты, %']).value,.8)
        self.assertEqual(s.cell(4,2).data_type,'s')
        self.assertEqual(s.freeze_panes,'C4')

    def test_auxiliary_inputs_are_grouped_before_zero_plan_substitution(self):
        sql=gates_sql().replace('motivation.PlanFactTest2025','pf').replace("N'","'")
        sql=sql.replace('CROSS JOIN (VALUES(1),(2),(3),(4),(5)) p(Period_)',
            'CROSS JOIN (SELECT 1 AS Period_ UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5) p')
        with sqlite3.connect(':memory:') as db:
            db.create_function('PATINDEX',2,lambda pattern,value:next((i+1 for i,c in enumerate(value or '') if c not in '0123456789. '),0))
            db.execute('CREATE TABLE pf (ТипКоэф,Подразделение,АвторСделки,Квартал,СуммаПланUSD,СуммаОтгрузокUSD,Год,Канал,ПодразделениеДокумент,ГруппаПлановПродаж,ГруппаТоваров,Марка,K0,K1,K2,K3,K4,VariationCalculate)')
            base=('Продажи','Branch','Продажи',1)
            tail=(2025,'ДПП','ID','HEAT','G','B',0,0,0,0,0,'Подразделение')
            db.executemany('INSERT INTO pf VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[
                (*base,0,30,*tail),(*base,0,40,*tail),(*base,100,0,*tail)])
            rows=db.execute(sql).fetchall()
        q2=next(row for row in rows if row[3]==2)
        self.assertEqual(q2[4:],(100,100,70))
        self.assertTrue(any(row[3]==5 for row in rows))

    def test_threshold_comes_from_the_job_and_auxiliary_failure_is_distinct(self):
        r=explain([detail(fact=85)],[],'.9')[0]
        self.assertEqual(r['threshold'],Decimal('.9'))
        self.assertIn('по формуле ниже порога',r['reason'])
        r=explain([detail(fact=90)],[('Политики','Branch','Author',2,100,100,20)],'.8')[0]
        self.assertNotIn('по формуле ниже порога',r['reason'])
        self.assertIn('сотрудника ниже порога',r['reason'])

    def test_export_reader_attaches_diagnostics_after_the_same_run_check(self):
        from .reports_2025 import read_results
        from .services.execution_tracking import parameters
        import json
        job=SimpleNamespace(key='job',variant='test_2025',fact_year=2025,plan_version='Plan',
            quarter_share=80,year_share=20,payout_threshold='.8',recalculation_2025=SimpleNamespace(source_sha256='hash'))
        connection,cursor=MagicMock(),MagicMock()
        cursor.fetchone.side_effect=[(0,),('job','completed','fact25-plan25-v4-consistent-goods-keys',json.dumps(parameters(job)))]
        cursor.fetchall.side_effect=[
            [('Политики','Branch','Author','2 Квартал(1-2 кв)',0,0)],
            [detail()],[('Политики','Branch','Author',2,100,100,70)]]
        result=read_results(job,lambda *a:(connection,cursor),include_performance=True)
        self.assertEqual(result[0]['performance'][0]['ratio'],Decimal('.7'))
        self.assertEqual(result[0]['total'],0)
        sql=[call.args[0] for call in cursor.execute.call_args_list]
        self.assertLess(next(i for i,s in enumerate(sql) if 'PortalMotivationRun' in s),sql.index(DETAIL_SQL))
        connection.close.assert_called_once()
