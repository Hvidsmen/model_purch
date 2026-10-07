import logging
import pyodbc
from collections import defaultdict
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction

from .models import ScenarioModel, ScenarioPlanSales, Purch, PurchPay, PGGoods, KindPurch

logger = logging.getLogger(__name__)
MS_SQL_CONN_STR = getattr(settings, 'MS_SQL_CONN_STR', None)


def export_scenario_to_sql(request, pk):
    """
    Экспортирует данные сценария в MS SQL Server (схема portal).
    Автоматически создаёт таблицы, если они не существуют.
    """
    scenario = get_object_or_404(ScenarioModel, pk=pk)

    if not MS_SQL_CONN_STR:
        messages.error(request, "Не настроено подключение к MS SQL Server")
        return redirect('scenario_list')

    conn = None
    try:
        conn = pyodbc.connect(MS_SQL_CONN_STR)
        cursor = conn.cursor()

        # 1. Создаём схему portal, если её нет
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'portal')
                EXEC('CREATE SCHEMA portal')
        """)
        conn.commit()

        # 2. Создаём таблицы, если их нет

        # Таблица Scenario (основная информация о сценарии)
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'portal.Scenario') AND type = 'U')
            CREATE TABLE portal.Scenario (
                id INT PRIMARY KEY,
                name NVARCHAR(255) NOT NULL,
                date_start_plan DATE NULL,
                date_end_plan DATE NULL,
                overwrite_existing BIT NULL DEFAULT 0
            )
        """)

        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'portal.Purch') AND type = 'U')
            CREATE TABLE portal.Purch (
                id INT IDENTITY(1,1) PRIMARY KEY,
                name NVARCHAR(255) NOT NULL,
                lag_income INT NULL,
                scenario_name NVARCHAR(255) NULL,
                CONSTRAINT UQ_Purch_name_scenario UNIQUE (name, scenario_name)
            )
        """)

        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'portal.PurchPay') AND type = 'U')
            CREATE TABLE portal.PurchPay (
                id INT IDENTITY(1,1) PRIMARY KEY,
                purch_id INT NOT NULL,
                name NVARCHAR(255) NULL,
                percent_pay DECIMAL(10,2) NULL,
                lag_day_pay INT NULL,
                CONSTRAINT FK_PurchPay_Purch FOREIGN KEY (purch_id) REFERENCES portal.Purch(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'portal.PGGoods') AND type = 'U')
            CREATE TABLE portal.PGGoods (
                id INT IDENTITY(1,1) PRIMARY KEY,
                planning_group NVARCHAR(255) NULL,
                planning_sales NVARCHAR(255) NULL,
                group_goods NVARCHAR(255) NULL,
                brand NVARCHAR(255) NULL,
                purch NVARCHAR(255) NULL,
                volume DECIMAL(18,4) NULL,
                exw_usd DECIMAL(18,2) NULL,
                ddp_usd DECIMAL(18,2) NULL,
                kddp DECIMAL(18,4) NULL,
                stock_cnt_day INT NULL,
                percent_stock_end DECIMAL(5,2) NULL,
                scenario_name NVARCHAR(255) NULL,
                kind_purch NVARCHAR(255) NULL,
                CONSTRAINT UQ_PGGoods_scenario UNIQUE (planning_group, planning_sales, group_goods, scenario_name)
            )
        """)

        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'portal.ScenarioPlanSales') AND type = 'U')
            CREATE TABLE portal.ScenarioPlanSales (
                id INT IDENTITY(1,1) PRIMARY KEY,
                name NVARCHAR(255) NULL,
                scenario_name NVARCHAR(255) NULL,
                flag_order_in_purch BIT NULL DEFAULT 0,
                CONSTRAINT UQ_SPS_scenario UNIQUE (name, scenario_name)
            )
        """)
        conn.commit()

        exported_count = {'scenario': 0, 'purch': 0, 'purchpay': 0, 'pggoods': 0, 'plans': 0}

        with transaction.atomic():
            # === ЭКСПОРТ ОСНОВНОЙ ИНФОРМАЦИИ О СЦЕНАРИИ ===
            # Явно конвертируем типы для совместимости со старым ODBC драйвером
            scenario_id = int(scenario.id)
            scenario_name = str(scenario.name) if scenario.name else ''
            date_start = scenario.date_start_plan.strftime('%Y-%m-%d') if scenario.date_start_plan else None
            date_end = scenario.date_end_plan.strftime('%Y-%m-%d') if scenario.date_end_plan else None
            overwrite_flag = 1 if scenario.overwrite_existing else 0

            cursor.execute("""
                MERGE INTO portal.Scenario AS target
                USING (
                    SELECT 
                        ? AS id,
                        ? AS name,
                        CAST(? AS DATE) AS date_start_plan,
                        CAST(? AS DATE) AS date_end_plan,
                        CAST(? AS BIT) AS overwrite_existing
                ) AS source
                ON target.id = source.id
                WHEN MATCHED THEN
                    UPDATE SET 
                        name = source.name,
                        date_start_plan = source.date_start_plan,
                        date_end_plan = source.date_end_plan,
                        overwrite_existing = source.overwrite_existing
                WHEN NOT MATCHED THEN
                    INSERT (id, name, date_start_plan, date_end_plan, overwrite_existing)
                    VALUES (source.id, source.name, source.date_start_plan, source.date_end_plan, source.overwrite_existing);
            """,
                           scenario_id,
                           scenario_name,
                           date_start,
                           date_end,
                           overwrite_flag
                           )
            exported_count['scenario'] += 1

            # === ЭКСПОРТ ПЛАНОВ ПРОДАЖ ===
            plans = ScenarioPlanSales.objects.filter(scenario_model=scenario)
            for plan in plans:
                cursor.execute("""
                    MERGE INTO portal.ScenarioPlanSales AS target
                    USING (SELECT ? AS name, ? AS scenario_name, ? AS flag_order) AS source
                    ON target.name = source.name AND target.scenario_name = source.scenario_name
                    WHEN MATCHED THEN
                        UPDATE SET flag_order_in_purch = source.flag_order
                    WHEN NOT MATCHED THEN
                        INSERT (name, scenario_name, flag_order_in_purch)
                        VALUES (source.name, source.scenario_name, source.flag_order);
                """, plan.name, scenario.name, plan.flag_order_in_purch)
                exported_count['plans'] += 1

            # === ЭКСПОРТ ЗАКУПОК И ПЛАТЕЖЕЙ ===
            purchs = Purch.objects.filter(scenario_plan=scenario).prefetch_related('purchpay_set')

            for purch in purchs:
                # Вставляем/обновляем закупку
                cursor.execute("""
                    MERGE INTO portal.Purch AS target
                    USING (SELECT ? AS name, ? AS lag_income, ? AS scenario_name) AS source
                    ON target.name = source.name AND target.scenario_name = source.scenario_name
                    WHEN MATCHED THEN
                        UPDATE SET lag_income = source.lag_income
                    WHEN NOT MATCHED THEN
                        INSERT (name, lag_income, scenario_name)
                        VALUES (source.name, source.lag_income, source.scenario_name)
                    OUTPUT inserted.id;
                """, purch.name, purch.lag_income, scenario.name)

                row = cursor.fetchone()
                purch_id = row[0] if row else None

                if purch_id:
                    # Удаляем старые платежи этой закупки в этом сценарии
                    cursor.execute("""
                        DELETE FROM portal.PurchPay 
                        WHERE purch_id = ?
                    """, purch_id)

                    # Вставляем новые платежи
                    for pay in purch.purchpay_set.all():
                        cursor.execute("""
                            INSERT INTO portal.PurchPay (purch_id, name, percent_pay, lag_day_pay)
                            VALUES (?, ?, ?, ?)
                        """, purch_id, pay.name, pay.percent_pay, pay.lag_day_pay)
                        exported_count['purchpay'] += 1

                    exported_count['purch'] += 1

            # === ЭКСПОРТ ТОВАРОВ ===
            pggoods_list = PGGoods.objects.filter(scenario_plan=scenario)

            for pg in pggoods_list:
                cursor.execute("""
                    MERGE INTO portal.PGGoods AS target
                    USING (
                        SELECT 
                            ? AS planning_group,
                            ? AS planning_sales,
                            ? AS group_goods,
                            ? AS brand,
                            ? AS purch,
                            ? AS volume,
                            ? AS exw_usd,
                            ? AS ddp_usd,
                            ? AS kddp,
                            ? AS stock_cnt_day,
                            ? AS percent_stock_end,
                            ? AS scenario_name,
                            ? kind_purch
                    ) AS source
                    ON target.planning_group = source.planning_group 
                       AND target.planning_sales = source.planning_sales
                       AND target.group_goods = source.group_goods
                       AND target.scenario_name = source.scenario_name
                    WHEN MATCHED THEN
                        UPDATE SET 
                            brand = source.brand,
                            purch = source.purch,
                            volume = source.volume,
                            exw_usd = source.exw_usd,
                            ddp_usd = source.ddp_usd,
                            kddp = source.kddp,
                            stock_cnt_day = source.stock_cnt_day,
                            percent_stock_end = source.percent_stock_end,
                            kind_purch = source.kind_purch
                    WHEN NOT MATCHED THEN
                        INSERT (planning_group, planning_sales, group_goods, brand, purch, 
                                volume, exw_usd, ddp_usd, kddp, stock_cnt_day, 
                                percent_stock_end, scenario_name, kind_purch)
                        VALUES (source.planning_group, source.planning_sales, source.group_goods,
                                source.brand, source.purch, source.volume, source.exw_usd,
                                source.ddp_usd, source.kddp, source.stock_cnt_day,
                                source.percent_stock_end, source.scenario_name, source.kind_purch);
                """,
                               pg.planning_group, pg.planning_sales, pg.group_goods,
                               pg.brand, pg.purch, pg.volume, pg.exw_usd, pg.ddp_usd,
                               pg.kddp, pg.stock_cnt_day, pg.percent_stock_end, scenario.name, pg.kind_purch.name
                               )
                exported_count['pggoods'] += 1

            conn.commit()

        total = sum(exported_count.values())
        messages.success(
            request,
            f'✅ Сценарий "{scenario.name}" экспортирован в MS SQL! '
            f'Сценарий: {exported_count["scenario"]}, '
            f'Планов: {exported_count["plans"]}, '
            f'Закупок: {exported_count["purch"]}, '
            f'Платежей: {exported_count["purchpay"]}, '
            f'Товаров: {exported_count["pggoods"]}'
        )

    except pyodbc.Error as e:
        logger.error(f"Ошибка экспорта в MS SQL: {e}")
        messages.error(request, f'Ошибка экспорта в MS SQL: {e}')
        if conn:
            conn.rollback()
    except Exception as e:
        logger.error(f"Ошибка при экспорте сценария: {e}")
        messages.error(request, f'❌ Ошибка при экспорте: {e}')
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return redirect('scenario_list')



def sync_purch_data_for_scenario(scenario):
    """
    Синхронизация Purch. Если scenario.overwrite_existing=True — обновляет существующие,
    иначе только создаёт новые.
    """
    if not MS_SQL_CONN_STR:
        raise ValueError("Не задана строка подключения MS_SQL_CONN_STR в settings.py")

    sql = """
        SELECT 
            IIF([Purch] ='','Без поставщика',[Purch]) AS PurchName,
            lag_ AS lag_income,
            ТипПлатежа AS PurchPayName,
            ПроцентОплаты AS percent_pay,
            Дней AS lag_day_pay
        FROM (
            SELECT 
                [Purch], 120 AS lag_,
                COALESCE(cu.ТипПлатежа, '') AS ТипПлатежа,
                COALESCE(cu.Дней, 0) AS Дней,
                COALESCE(cu.ПроцентОплаты, 0) AS ПроцентОплаты,
                ROW_NUMBER() OVER(PARTITION BY [Purch], COALESCE(cu.ТипПлатежа, '') ORDER BY [Purch]) AS RowID
            FROM [ModelPurch].[dbo].[PlanningGroupOZP] p
            LEFT JOIN DataWH.erp.[Справочники.ДоговорыКонтрагентов] c ON p.Purch = c.Партнер
            LEFT JOIN DataWH.erp.[Справочник.ДоговорыКонтрагентов_Даичи_УсловияОплаты] cu ON cu.Ссылка = c.Ссылка
        ) t
        WHERE RowID = 1
    """
    conn = None
    try:
        conn = pyodbc.connect(MS_SQL_CONN_STR)
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [column[0] for column in cursor.description]
        raw_data = [dict(zip(columns, row)) for row in cursor.fetchall()]

        purch_groups = defaultdict(list)
        for row in raw_data:
            purch_groups[row['PurchName']].append(row)

        overwrite = scenario.overwrite_existing
        created_count = 0
        updated_count = 0
        skipped_count = 0

        with transaction.atomic():
            for purch_name, payments in purch_groups.items():
                if overwrite:
                    # РЕЖИМ ПЕРЕЗАПИСИ: get_or_create + обновление + замена платежей
                    purch, created = Purch.objects.get_or_create(
                        name=purch_name, scenario_plan=scenario,
                        defaults={'lag_income': int(payments[0]['lag_income'] or 120)}
                    )
                    if not created:
                        purch.lag_income = int(payments[0]['lag_income'] or 120)
                        purch.save()
                        updated_count += 1
                    else:
                        created_count += 1

                    # Полностью заменяем платежи
                    purch.purchpay_set.all().delete()
                    PurchPay.objects.bulk_create([
                        PurchPay(purch=purch, name=row['PurchPayName'] or 'Без названия',
                                 percent_pay=float(row['percent_pay'] or 0.0),
                                 lag_day_pay=int(row['lag_day_pay'] or 0))
                        for row in payments
                    ])
                else:
                    # РЕЖИМ БЕЗ ПЕРЕЗАПИСИ: только создаём новые, существующие пропускаем
                    purch, created = Purch.objects.get_or_create(
                        name=purch_name, scenario_plan=scenario,
                        defaults={'lag_income': int(payments[0]['lag_income'] or 120)}
                    )
                    if created:
                        created_count += 1
                        # Создаём платежи только для новых закупок
                        PurchPay.objects.bulk_create([
                            PurchPay(purch=purch, name=row['PurchPayName'] or 'Без названия',
                                     percent_pay=float(row['percent_pay'] or 0.0),
                                     lag_day_pay=int(row['lag_day_pay'] or 0))
                            for row in payments
                        ])
                    else:
                        skipped_count += 1

        logger.info(
            f"Синхронизация Purch для '{scenario.name}' (overwrite={overwrite}): "
            f"создано={created_count}, обновлено={updated_count}, пропущено={skipped_count}"
        )
    finally:
        if conn: conn.close()


def sync_pggoods_data_for_scenario(scenario):
    """
    Синхронизация PGGoods. Если scenario.overwrite_existing=True — обновляет существующие,
    иначе только создаёт новые.
    """

    if not MS_SQL_CONN_STR:
        raise ValueError("Не задана строка подключения MS_SQL_CONN_STR в settings.py")

    scenario_obj = ScenarioModel.objects.get(id=scenario.id)
    print(scenario_obj)
    plans_obj = ScenarioPlanSales.objects.filter(scenario_model=scenario_obj)
    print(scenario_obj)

    str_scenarios = ", ".join([f"'{p.name}'" for p in plans_obj])
    print(str_scenarios)
    scenario_id = int(scenario.id)
    scenario_name = str(scenario.name) if scenario.name else ''
    date_start = scenario.date_start_plan.strftime('%Y-%m-%d') if scenario.date_start_plan else None
    date_end = scenario.date_end_plan.strftime('%Y-%m-%d') if scenario.date_end_plan else None
    overwrite_flag = 1 if scenario.overwrite_existing else 0
    sql = f"""

;With plan_ AS (
SELECT
        mce.PlanningGroupOZPErp
        , SUM(СуммаДДП) / SUM(Количество) AS PriceDDP

FROM
        ModelPurch.dbo.PlanSalesERPAll pe
        INNER JOIN DataWH.dbo.ModelCodeERP mce
        ON pe.Номенклатура = mce.ModelCode
WHERE
Сценарий IN ({str_scenarios})
AND pe.Date_ BETWEEN '{date_start}' AND '{date_end}'

GROUP BY
        mce.PlanningGroupOZPErp
HAVING
        SUM(Количество) > 0
)
,stock AS (

SELECT
        ReportDate
        ,Номенклатура
        ,n.Даичи_ГруппаПланирования ГруппаПланирования

        ,SUM((ВНаличии + ВПути)*mce.FlagAccountingQtyBA) ВНаличии
FROM
        DataWH.erp.StreamGoods sg
        INNER JOIN DataWH.erp.[Справочники.Номенклатура] n
                ON sg.НоменклатураСсылка = n.Ссылка
        INNER JOIN DataWH.dbo.ModelCodeERP mce
                ON mce.ModelCode = n.Наименование
        INNER JOIN DataWH.erp.[Справочник.Склады] store
                ON store.Ссылка = sg.СкладСсылка
                AND store.даичи_НазначениеСклада = 'Продажа'
WHERE
        sg.ReportDate = '{date_start}'
GROUP BY
        ReportDate
        ,Номенклатура
        ,n.Даичи_ГруппаПланирования

HAVING
        SUM(ВНаличии + ВПути)>0
)
,income  AS (
SELECT
        CASE
                WHEN ТипПоступления = 'ЗаказПоставщику' THEN DATEADD(MONTH,2,IIF(ДатаПоступления<=ReportDate,DATEADD(DAY,14,ReportDate),ДатаПоступления))
                else IIF(ДатаПоступления<=ReportDate,DATEADD(DAY,14,ReportDate),ДатаПоступления)
        END ДатаПоступления
        ,Номенклатура
        ,n.Даичи_ГруппаПланирования ГруппаПланирования
        ,ТипПоступления
        ,SUM((ВПути + КПоступлению)*mce.FlagAccountingQtyBA) ВНаличии

FROM
        DataWH.erp.StreamGoods sg
        INNER JOIN DataWH.erp.[Справочники.Номенклатура] n
                ON sg.НоменклатураСсылка = n.Ссылка
        INNER JOIN DataWH.dbo.ModelCodeERP mce
                ON mce.ModelCode = n.Наименование
        INNER JOIN DataWH.erp.[Справочник.Склады] store
                ON store.Ссылка = sg.СкладСсылка


WHERE
        (ТипПоступления IN ('ЗаказПоставщику','ПриобретениеТоваровУслуг')
        OR (ТипПоступления ='ПеремещениеТоваров' AND даичи_НазначениеСклада!= 'Продажа'))
        AND ReportDate ='{date_start}'
        AND  CASE
                WHEN ТипПоступления = 'ЗаказПоставщику' THEN DATEADD(MONTH,2,IIF(ДатаПоступления<=ReportDate,DATEADD(DAY,14,ReportDate),ДатаПоступления))
                else IIF(ДатаПоступления<=ReportDate,DATEADD(DAY,14,ReportDate),ДатаПоступления)
        END BETWEEN '{date_start}' AND '{date_end}'
GROUP BY
        CASE
                WHEN ТипПоступления = 'ЗаказПоставщику' THEN DATEADD(MONTH,2,IIF(ДатаПоступления<=ReportDate,DATEADD(DAY,14,ReportDate),ДатаПоступления))
                else IIF(ДатаПоступления<=ReportDate,DATEADD(DAY,14,ReportDate),ДатаПоступления)
        END
        ,Номенклатура
        ,n.Даичи_ГруппаПланирования
        ,ТипПоступления
HAVING
        SUM(ВПути + КПоступлению)>0
)
SELECT
        p.[PlanningGroupSalesERP], p.[PlanningKey], p.[Group_1], p.[BrandName],
        p.[PlanningGroupOZP], IIF(p.[Purch]='', 'Без поставщика',p.[Purch]) Purch, p.[FlagInPlan], p.[Volume], ddp.PriceDDP
FROM [ModelPurch].[dbo].[PlanningGroupOZP] p
INNER JOIN (
        SELECT
                PlanningGroupOZPErp
                , PriceDDP
        FROM
                plan_
        UNION
        SELECT DISTINCT
                g.PlanningGroupOZP
                ,COALESCE(ps.PriceDDP,0)
        FROM
                stock s
                INNER JOIN ModelPurch.dbo.Goods g
                        ON s.Номенклатура = g.PlanningKey
                LEFT JOIN plan_ ps
                        ON g.PlanningGroupOZP = ps.PlanningGroupOZPErp
        UNION
        SELECT DISTINCT
                g.PlanningGroupOZP
                ,COALESCE(ps.PriceDDP,0)
        FROM
                income s
                INNER JOIN ModelPurch.dbo.Goods g
                        ON s.Номенклатура = g.PlanningKey
                LEFT JOIN plan_ ps
                        ON g.PlanningGroupOZP = ps.PlanningGroupOZPErp

) ddp ON p.PlanningGroupOZP = ddp.PlanningGroupOZPErp

    """
    print(sql)
    conn = None
    try:
        conn = pyodbc.connect(MS_SQL_CONN_STR)
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [column[0] for column in cursor.description]
        raw_data = [dict(zip(columns, row)) for row in cursor.fetchall()]

        overwrite = scenario.overwrite_existing
        created_count = 0
        updated_count = 0
        skipped_count = 0

        with transaction.atomic():
            for row in raw_data:
                planning_sales = str(row.get('PlanningGroupSalesERP') or '').strip()
                planning_group = str(row.get('PlanningGroupOZP') or '').strip()
                group_goods = str(row.get('Group_1') or '').strip()
                brand = str(row.get('BrandName') or '').strip() or None
                purch_name = str(row.get('Purch') or '').strip() or None
                volume = float(row.get('Volume') or 0.0)
                ddp_usd = float(row.get('PriceDDP') or 0.0)

                kind_purch_name = 'Закупается'
                kind_purch_obj, _ = KindPurch.objects.get_or_create(name=kind_purch_name)

                defaults = {
                    'brand': brand, 'purch': purch_name, 'kind_purch': kind_purch_obj,
                    'volume': volume, 'exw_usd': 0.0, 'ddp_usd': ddp_usd,
                    'kddp': 1.0 if ddp_usd > 0 else 0.0, 'stock_cnt_day': 0, 'percent_stock_end': 0.0,
                }

                if overwrite:
                    # РЕЖИМ ПЕРЕЗАПИСИ: update_or_create — обновляет существующие
                    # PGGoods.objects.filter(scenario_plan=scenario).delete()
                    pggoods, created = PGGoods.objects.update_or_create(
                        scenario_plan=scenario, planning_group=planning_group,
                        planning_sales=planning_sales, group_goods=group_goods,
                        defaults=defaults
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                else:
                    # РЕЖИМ БЕЗ ПЕРЕЗАПИСИ: только создаём новые
                    pggoods, created = PGGoods.objects.get_or_create(
                        scenario_plan=scenario, planning_group=planning_group,
                        planning_sales=planning_sales, group_goods=group_goods,
                        defaults=defaults
                    )
                    if created:
                        created_count += 1
                    else:
                        skipped_count += 1

        logger.info(
            f"Синхронизация PGGoods для '{scenario.name}' (overwrite={overwrite}): "
            f"создано={created_count}, обновлено={updated_count}, пропущено={skipped_count}"
        )
    finally:
        if conn: conn.close()