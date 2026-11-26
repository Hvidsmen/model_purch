"""Execute payout and eligibility expressions across a derived-table boundary."""
import re
import sqlite3
from django.test import SimpleTestCase
from .services import shareholder_original_rules as original, shareholder_regions as regions


class PayoutAliasTests(SimpleTestCase):
    def test_outer_source_resolves_derived_alias_and_legacy_table(self):
        self.assertEqual(original.payout_relation('SELECT 1 FROM #pf_base WHERE Квартал=3',8),('FROM #pf_base','#pf_base'))
        source='SELECT 1,COALESCE((SELECT MAX(x) FROM other),0) FROM (SELECT * FROM #pf_base WHERE Квартал=3) AS payout_scope'
        clause,alias=original.payout_relation(source,8)
        self.assertEqual(alias,'payout_scope')
        self.assertTrue(clause.startswith('FROM (SELECT * FROM #pf_base'))

    def test_native_and_regional_annual_eligibility_execute_from_same_derived_rows(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        columns=list(original.DIMENSIONS)+['Квартал','СуммаПланUSD','СуммаОтгрузокUSD','СуммаМотивацииUSDГод']
        db.execute('CREATE TABLE pf_base ('+','.join('['+key+']' for key in columns)+')')
        db.execute('CREATE TABLE region_department(Подразделение,Год,Period_,Execution_)')
        for branch in ('Д-Москва','Д-Ростов'):
            db.execute('INSERT INTO region_department VALUES(?,2026,5,0.9)',(branch,))
            for quarter in range(1,5):
                values=[branch,'Иван',2026,'RAC','Goods','Brand','Политики','Подразделение',quarter,100.,90.,10.]
                db.execute('INSERT INTO pf_base VALUES('+','.join('?' for _ in values)+')',values)
        derived=' FROM (SELECT *,IIF(SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение)/NULLIF(SUM(СуммаПланUSD) OVER(PARTITION BY Подразделение),0)<@PayoutThreshold,1,0) AS BelowThreshold FROM #pf_base WHERE (Квартал=4 OR @FactYear<>2026)) AS payout_scope'
        for regional in (False,True):
            predicate='payout_scope.BelowThreshold=1'
            if regional:predicate+=' OR '+regions.triple_failure(5,'payout_scope')
            source='SELECT 1 FROM #pf_base WHERE (Квартал=4 OR @FactYear<>2026); SELECT IIF('+predicate+',0,СуммаМотивацииUSDГод) AS Payment'+derived+';'
            restored=original.restore(source)
            query=restored[restored.index('SELECT DISTINCT '):].replace(' INTO #share_eligibility','').replace('#pf_base','pf_base').replace('#share_region_department','region_department').replace("N'","'")
            self.assertIn('AS payout_scope',query)
            self.assertNotIn('pf_base.[',query)
            for threshold,expected in ((.8,1),(.95,0)):
                rows=db.execute(query,{'PayoutThreshold':threshold,'FactYear':2026}).fetchall()
                self.assertEqual(len(rows),2)
                self.assertEqual({row[-1] for row in rows},{expected})

    def test_generated_period_gates_bind_to_the_actual_payout_alias(self):
        period_sources=[]
        for code in ('1','2','3','4','Год','Год'):
            label=code+' Квартал' if code!='Год' else code
            period_sources.append("SELECT '"+label+"' AS КварталМотивации,IIF(1<@PayoutThreshold,0,1) AS СуммаМотивацииUSDУчетПроцента,IIF(1<@PayoutThreshold,0,1) AS СуммаМотивацииRURУчетПроцента FROM (SELECT * FROM #pf_base) AS payout_scope")
        sql=regions.triple_gates(' UNION ALL '.join(period_sources))
        self.assertNotIn('#pf_base.[',sql)
        self.assertIn('d.[Подразделение]=payout_scope.[Подразделение]',sql)
        self.assertIn('d.[Год]=payout_scope.[Год]',sql)
