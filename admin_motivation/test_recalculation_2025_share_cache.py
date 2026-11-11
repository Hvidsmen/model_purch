import re
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from .test_recalculation_2025_share_fallback import HistoricalAuthorShareFallbackTests
from .services.recalculation_2025_sql import patch_source,template_source
from .services.recalculation_2025_share_cache import patch,CACHE,MARKER
from .services.recalculation_2025_share_fallback import SHARE_JOIN


class MaterializedShareFallbackTests(HistoricalAuthorShareFallbackTests):
    def prepare_cache(self):
        for name in ('test2025_policy_plan_rows','test2025_plan_scope_keys','test2025_available_scopes','test2025_selected_scopes','test2025_resolved_shares'):
            self.db.execute('DROP TABLE IF EXISTS '+name)
        self.db.execute('''CREATE TABLE test2025_policy_plan_rows AS SELECT rowid AS OriginalRowId,*,
 DENSE_RANK() OVER(ORDER BY SubdivisionKey,PGSales,Group_,Brand) AS PortalScopeId,
 SubdivisionKey AS PortalSubdivisionKey,PGSales AS PortalPGSales,Group_ AS PortalGroup_,Brand AS PortalBrand FROM target''')
        sql=CACHE.replace('#percent_plan_menger','shares').replace('#test2025_','test2025_')
        sql=re.sub(r'SELECT([\s\S]*?)\s+INTO\s+(\w+)\s+FROM',lambda m:'CREATE TABLE '+m[2]+' AS SELECT'+m[1]+' FROM',sql)
        sql=sql.replace(' CLUSTERED INDEX',' INDEX')
        self.db.executescript(sql)

    def allocate(self,branch,pg,goods,brand,plan=120):
        self.db.execute('DELETE FROM target')
        self.db.execute('INSERT INTO target VALUES (?,?,?,?,?)',(branch,pg,goods,brand,plan))
        self.prepare_cache()
        return self.db.execute('''SELECT COALESCE(pm.АвторСделки,'Не распределён'),
 t.AmountUSD*COALESCE(pm.Percent_,1),selected.AllocationLevel
 FROM test2025_policy_plan_rows t
 JOIN test2025_selected_scopes selected ON selected.PortalScopeId=t.PortalScopeId
 LEFT JOIN test2025_resolved_shares pm ON pm.PortalScopeId=t.PortalScopeId''').fetchall()

    def test_repeated_plan_keys_and_null_keys_preserve_each_plan_row(self):
        self.add('M','A','RAC','AC','X',30);self.add('M','B','RAC','AC','Y',70)
        self.add('TT','Foreign','RAC','AC','X',1000);self.build()
        self.db.executemany('INSERT INTO target VALUES (?,?,?,?,?)',[
            ('M','RAC','AC','Missing',120),('M','RAC','AC','Missing',240),
            ('M',None,None,None,60),('missing',None,None,None,90)])
        self.prepare_cache()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM test2025_plan_scope_keys').fetchone()[0],3)
        result=self.db.execute('''SELECT t.OriginalRowId,SUM(t.AmountUSD*COALESCE(pm.Percent_,1))
 FROM test2025_policy_plan_rows t LEFT JOIN test2025_resolved_shares pm
 ON pm.PortalScopeId=t.PortalScopeId GROUP BY t.OriginalRowId ORDER BY t.OriginalRowId''').fetchall()
        self.assertEqual(result,[(1,120),(2,240),(3,60),(4,90)])
        self.assertFalse(self.db.execute("SELECT 1 FROM test2025_resolved_shares WHERE АвторСделки='Foreign'").fetchall())


class ShareCacheShapeTests(SimpleTestCase):
    def test_materialization_removes_per_row_apply_but_preserves_sales_union(self):
        original=patch_source(template_source());updated=patch(original)
        self.assertEqual(patch(updated),updated)
        self.assertNotIn(SHARE_JOIN,updated)
        self.assertIn('DENSE_RANK() OVER(ORDER BY',updated)
        self.assertIn('MIN(pick.AllocationLevel)',updated)
        self.assertIn('pm.PortalScopeId=t.PortalScopeId',updated)
        self.assertEqual(original[original.index('UNION ALL',original.index('INTO #plan')):],
            updated[updated.index('UNION ALL',updated.index('INTO #plan')):])
        self.assertEqual(updated.count(MARKER),1)
        with self.assertRaises(ValidationError):patch(original.replace(SHARE_JOIN,''))
