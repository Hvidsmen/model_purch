# Sales plan import sources

Create or edit a sales plan scenario and choose its source. Existing scenarios remain `planning` after migration 0039. The scenario stores both source type and version; changing either requires confirmation to discard imported lines/results, and approved scenarios cannot change their source. Renaming alone preserves data.

* `planning`: DataWH.planning.PlanSales, filtered by Version_.
* `erp`: DataWH.erp.[РегистрыНакопления.ПланыПродаж], filtered by Сценарий.

Both use the existing vm-dwh/DataWH connection and bind the version as an SQL parameter. ERP maps Период to plan date, Подразделение to dbo.Subdivisions.SubdivisionKey, Номенклатура to planning.Goods.PlanningKey, Сумма to AmountUSD and даичи_СуммаДДП to AmountDDP_USD. These fields/joins follow existing warehouse queries in this repository (sp_CalcBySub.original.sql and erp_plan_parameters.sql). No currency conversion is introduced. Verify the ERP scenario contains amounts in USD before using it as a motivation plan.

The classification and original historical O0–O4 distribution remain the same for both sources. Missing segment history defaults to O0. Validation, original-subdivision/global aggregation and atomic replacement use the existing importer; empty/invalid source data retains the previous snapshot. Source lists are cached separately. Import does not publish coefficients or launch the coefficient-iteration scenario runner.

Local tests use mocked ODBC results and check parameter binding, field mappings, saved totals, source switching/reset, UI selection and preservation on empty imports. Live warehouse SQL execution is not available in the development environment.

Missing plan USD rule: the user confirmed that NULL plan AmountUSD means zero for all years and both sources, including ERP Сумма. The read-only query applies SUM(COALESCE(amount,0)) before deriving O0–O4. Non-null values, including negatives, remain unchanged. Optional DDP still retains its own missing-value semantics. No warehouse rows are updated; the imported snapshot stores zero for missing plan USD and its plan segments. Actual sales and calculated premiums are not zeroed by this rule.


Segment distribution repair: both planning and ERP imports derive O0–O4 weights from positive historical motivation turnover only (USD > 0); returns do not enter either numerator or denominator. Source catalog joins still use SubdivisionKey, while weight aggregation and matching use SubdivisionName and the normalized product dimensions. Thus all physical keys of a motivation unit share a bounded distribution. Each weight is in [0,1], their sum is one, and no positive history defaults to O0=1. Plan amounts, DDP and channels retain their original source receipts. Negative source plans retain their sign in each segment.

Import and calculation guards reject cancelling/opposite-sign or oversized source segments, even when their sum matches the plan. A rejected import preserves the previous loaded plan. Existing scenarios must be reloaded from MS SQL and recalculated; immutable prior iteration results are not rewritten. The actual historical sales used for payout calculation are not changed by this distribution rule.
