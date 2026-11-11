import re
import sqlite3
from unittest.mock import Mock
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from .services import calculation_stage_profile as profile, prepared_sales


class StageProfileTests(SimpleTestCase):
    def test_next_run_clears_every_slot_and_terminal_snapshot(self):
        sql=profile.begin('@run',('one','two'))
        for index in range(3):
            for key in profile.FIELDS:
                self.assertIn(f"@key=N'PortalProfile_{key}_{index}',@value=NULL",sql)
        self.assertNotIn('OPENJSON',sql)
        self.assertIn('WHERE session_id=@@SPID',sql)
        self.assertIn('END TRY BEGIN CATCH',sql)

    def intervals(self,failed):
        # Execute the actual generated window/delta logic with deterministic
        # snapshots, replacing only SQL Server input/conversion/persistence.
        sql=profile.finish(('copy','docs','discounts'),failed=failed)
        window=sql.split('), intervals AS (',1)[1].split('\n )',1)[0]
        select=sql.split('\n SELECT TRY_CONVERT(uniqueidentifier,',1)[1].split('\n FROM intervals',1)[0]
        select='SELECT '+select.split(')),',1)[1]
        select=select.replace("N'","'")
        select=select.replace('DATEDIFF_BIG(MILLISECOND,at,next_at)','next_at-at')
        conn=sqlite3.connect(':memory:')
        # Stage 1 was never reached. Missing counters and a counter reset must
        # remain NULL, and the failure belongs only to the final reached stage.
        rows=conn.execute('WITH snapshots(seq,label,at,cpu,logical,reads,writes) AS (VALUES '
            "(0,'copy',1000,10,100,5,NULL),(2,'discounts',4000,20,90,6,NULL),"
            "(3,'',6000,30,150,8,NULL)), intervals AS ("+window+') '+select+
            ' FROM intervals WHERE seq<3 AND next_at IS NOT NULL').fetchall()
        conn.close()
        return rows

    def test_skipped_stages_missing_counters_and_reset(self):
        rows=self.intervals(False)
        self.assertEqual([r[0] for r in rows],[0,2])
        self.assertEqual([r[4] for r in rows],[3000,2000])
        self.assertEqual(rows[0][5:9],(10,None,1,None))
        self.assertEqual([r[-1] for r in rows],['completed','completed'])

    def test_handled_error_marks_only_last_reached_stage(self):
        self.assertEqual([r[-1] for r in self.intervals(True)],['completed','failed'])
        sql=profile.finish(('one',),failed=True)
        self.assertIn('AND @@TRANCOUNT=0',sql)
        self.assertIn('END TRY BEGIN CATCH',sql)

    def test_producer_persists_after_commit_or_rollback_and_unique_declarations(self):
        sql=prepared_sales.procedure_sql()
        insert='INSERT INTO '+profile.TABLE
        self.assertEqual(sql.count(insert),2)
        self.assertLess(sql.index('COMMIT TRANSACTION;'),sql.index(insert))
        self.assertLess(sql.index('IF XACT_STATE()<>0 ROLLBACK TRANSACTION;'),sql.rindex(insert))
        declarations=[]
        for statement in re.findall(r'\bDECLARE\s+([^;]+);',sql,re.I):
            declarations+=re.findall(r'(@portalProfile_\w+)\s+(?:bigint|datetime2)',statement)
        self.assertEqual(len(declarations),len(set(declarations)))
        self.assertEqual(len(declarations),40)

    def test_read_is_bound_and_missing_old_table_is_allowed(self):
        cursor=Mock();cursor.fetchall.return_value=[(2,'docs',1234,None,0,3,4,'completed')]
        rows=profile.read(cursor,'run-id')
        self.assertEqual(cursor.execute.call_args.args[1],'run-id')
        self.assertEqual(rows[0]['seconds'],1.234)
        self.assertIsNone(rows[0]['cpu_ms']);self.assertEqual(rows[0]['logical_reads'],0)
        cursor.execute.side_effect=RuntimeError('old schema')
        self.assertEqual(profile.read(cursor,'run-id'),[])

    def test_template_retains_zero_and_escapes_stage_name(self):
        html=render_to_string('admin_motivation/includes/calculation_stage_profile.html',{'stages':[
            dict(stage='<test>',seconds=1,cpu_ms=None,logical_reads=0,reads=2,writes=0,state='failed')]})
        self.assertIn('&lt;test&gt;',html);self.assertIn('<td>0</td>',html)
        self.assertIn('Ошибка',html);self.assertIn('—',html)
