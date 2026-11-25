# Purchasing ERP identity: PlanningKey

The user confirmed that the ERP goods code for this matrix is DataWH.planning.Goods.PlanningKey. Goods directory refresh now retains the PlanningKey of its deterministic representative source row in PGGoods.source_code, alongside classification. Newly appended goods and the legacy full synchronization store the same value. The code is shown under the planning-group label in the matrix. Existing empty codes are populated by refreshing the directory; no PlanningKey is guessed for a historical unnamed record with no source identity.

The SQL export writes an explicit portal.PGGoods.PlanningKey column and retains source_code and the planning-group label as separate fields. Goods with a PlanningKey use its namespaced normalized identity plus scenario for export matching; unnamed rows without a code retain their distinct identity, and legacy named rows without a code retain legacy matching. Duplicate populated codes in a selected portal scenario block export. A unique legacy named row can be adopted on its first code-based export instead of inserting a duplicate. The legacy uniqueness index on scenario/name is removed; code/key uniqueness remains. SQL preparation no longer merges different codes or unrelated empty names together. Changes use the existing SQL transaction and duplicate archive.

This changes loading and export, not the warehouse procedure definitions. The existing Python calculation joins TableForCalcPlanIncomes to portal.vPGGoods_Lag by planning-group name, and dbo.sp_ETLDataBase, dbo.sp_CreateTableModel and dbo.sp_Date exist only in the corporate SQL Server. They must be reviewed together before changing the calculation key. A blank planning group therefore remains blocked by calculation preflight until that mapping is implemented.

To obtain the required definitions on the portal VM, run from the project directory with the portal Python environment:

    python manage.py inspect_purchase_sql --output .local/purchase-sql-objects.json

The command only reads sys.objects, sys.sql_modules and sys.columns, does not execute the procedures, and writes a UTF-8 JSON file locally. Send that file for the SQL/procedure and Python calculation update. Missing definitions require VIEW DEFINITION permission; no credentials or business table rows are exported.

Tests cover representative PlanningKey capture, SQL parameter binding and code identity, distinct unnamed/code rows, legacy export compatibility and read-only inspection. Corporate SQL execution is not available in the cloud workspace. No Django migration is needed beyond existing migration 0036; the additive SQL column is created by export.
