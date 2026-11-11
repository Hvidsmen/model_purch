# Sales plan import sources

Create or edit a sales plan scenario and choose its source. Existing scenarios remain `planning` after migration 0039. The scenario stores both source type and version; changing either requires confirmation to discard imported lines/results, and approved scenarios cannot change their source. Renaming alone preserves data.

* `planning`: DataWH.planning.PlanSales, filtered by Version_.
* `erp`: DataWH.erp.[РегистрыНакопления.ПланыПродаж], filtered by Сценарий.

Both use the existing vm-dwh/DataWH connection and bind the version as an SQL parameter. ERP maps Период to plan date, Подразделение to dbo.Subdivisions.SubdivisionKey, Номенклатура to planning.Goods.PlanningKey, Сумма to AmountUSD and даичи_СуммаДДП to AmountDDP_USD. These fields/joins follow existing warehouse queries in this repository (sp_CalcBySub.original.sql and erp_plan_parameters.sql). No currency conversion is introduced. Verify the ERP scenario contains amounts in USD before using it as a motivation plan.

The classification and original historical O0–O4 distribution remain the same for both sources. Missing segment history defaults to O0. Validation, original-subdivision/global aggregation and atomic replacement use the existing importer; empty/invalid source data retains the previous snapshot. Source lists are cached separately. Import does not publish coefficients or launch the coefficient-iteration scenario runner.

Local tests use mocked ODBC results and check parameter binding, field mappings, saved totals, source switching/reset, UI selection and preservation on empty imports. Live warehouse SQL execution is not available in the development environment.
