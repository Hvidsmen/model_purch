SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT = 0
    BEGIN TRANSACTION;

-- Serialize simultaneous refreshes of the shared source table.
DECLARE @lock_result int;
EXEC @lock_result = sys.sp_getapplock
    @Resource = N'model_purch.refresh_PlanningGroupOZP',
    @LockMode = N'Exclusive',
    @LockOwner = N'Transaction',
    @LockTimeout = 60000;
IF @lock_result < 0
    THROW 50001, N'Не удалось дождаться обновления справочника товаров. Повторите попытку.', 1;

IF OBJECT_ID(N'[ModelPurch].dbo.PlanningGroupOZP', N'U') IS NOT NULL
    DROP TABLE [ModelPurch].dbo.PlanningGroupOZP;

SELECT
    PlanningGroupSalesERP,
    PlanningKey,
    Group_1,
    BrandName,
    PlanningGroupOZP,
    Purch,
    FlagInPlan,
    Volume
INTO [ModelPurch].dbo.PlanningGroupOZP
FROM (
    SELECT
        g.PlanningGroupSalesERP,
        g.Group_1,
        g.PlanningKey,
        g.BrandName,
        g.PlanningGroupOZP,
        g.Purch,
        COALESCE(mce.Volume, 0) Volume,
        ROW_NUMBER() OVER (
            PARTITION BY g.PlanningGroupOZP
            ORDER BY IIF(g.PlanningKey LIKE 'PG%', '00', CONCAT('01', g.PlanningGroupOZP))
        ) RowId,
        IIF(g.PlanningGroupOZP IN (
            SELECT DISTINCT fs.PlanningGroupOZP FROM [ModelPurch].dbo.GoodsPlan fs
        ), 1, 0) FlagInPlan
    FROM DataWH.planning.Goods g
    LEFT JOIN DataWH.dbo.ModelCodeERP mce ON g.PlanningKey = mce.ModelCode
) t
WHERE RowId = 1;
