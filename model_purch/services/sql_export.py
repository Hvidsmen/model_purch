import logging
import pyodbc
from django.contrib import messages
from django.db import transaction
from ..models import ScenarioModel, ScenarioPlanSales, Purch, PurchPay, PGGoods, KindLagPay, Freight, ScenarioExport
from ..sql_goods import prepare_sql_goods
from ..goods_identity import planning_group_key
from ..sql_export_fields import ensure_model_columns, ensure_column, export_additional_fields

from .preflight import snapshot, fingerprint
from .purchases import coverage_errors

logger = logging.getLogger(__name__)

def export_scenario(request, scenario, connection_string):
    """
    Экспортирует данные сценария в MS SQL Server (схема portal).
    Автоматически создаёт таблицы, если они не существуют.
    """

    errors = coverage_errors(scenario)
    if errors:
        for error in errors:
            messages.error(request, f'«{scenario.name}»: {error}')
        return False

    if not connection_string:
        messages.error(request, "Не настроено подключение к MS SQL Server")
        return False

    conn = None
    succeeded = False
    try:
        conn = pyodbc.connect(connection_string)
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
                planning_group_key CHAR(64) NOT NULL
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
        cursor.execute("""
            IF OBJECT_ID(N'portal.KindLagPay', N'U') IS NULL
                CREATE TABLE portal.KindLagPay (id INT PRIMARY KEY, name NVARCHAR(255) NOT NULL);
        """)
        cursor.execute("""
            IF OBJECT_ID(N'portal.Freight', N'U') IS NULL
                CREATE TABLE portal.Freight (
                    scenario_id INT PRIMARY KEY REFERENCES portal.Scenario(id),
                    scenario_name NVARCHAR(255) NOT NULL,
                    price_per_container DECIMAL(18,2) NOT NULL,
                    volume_per_container DECIMAL(12,3) NOT NULL
                );
        """)
        for table, model, relations in [
            ('Scenario', ScenarioModel, set()),
            ('Freight', Freight, {'scenario'}),
            ('ScenarioPlanSales', ScenarioPlanSales, {'scenario_model'}),
            ('Purch', Purch, set()),
            ('PurchPay', PurchPay, {'purch', 'kind_lag_pay'}),
            ('PGGoods', PGGoods, {'scenario_plan', 'kind_purch'}),
            ('KindLagPay', KindLagPay, set()),
        ]:
            ensure_model_columns(cursor, table, model, relations, {'name_key'} if model is Purch else ())
        ensure_column(cursor, 'PurchPay', 'kind_lag_pay_id', 'INT')
        ensure_column(cursor, 'PurchPay', 'kind_lag_pay', 'NVARCHAR(255)')
        conn.commit()

        exported_count = {'scenario': 0, 'purch': 0, 'purchpay': 0, 'pggoods': 0, 'plans': 0, 'freight': 0}

        with transaction.atomic():
            parameters = snapshot(scenario)
            prepare_sql_goods(cursor)
            # Reference IDs and labels must be available before payment export.
            for lag_kind in KindLagPay.objects.all():
                cursor.execute("""
                    MERGE INTO portal.KindLagPay AS target
                    USING (SELECT ? AS id, ? AS name) AS source
                    ON target.id = source.id
                    WHEN MATCHED THEN UPDATE SET name = source.name
                    WHEN NOT MATCHED THEN INSERT (id, name) VALUES (source.id, source.name);
                """, lag_kind.pk, lag_kind.name)
                export_additional_fields(cursor, 'KindLagPay', lag_kind, {'name'}, {'id': lag_kind.pk})
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
            export_additional_fields(cursor, 'Scenario', scenario,
                                     {'name', 'date_start_plan', 'date_end_plan', 'overwrite_existing'}, {'id': scenario.pk})
            exported_count['scenario'] += 1

            freight = Freight.objects.filter(scenario=scenario).first()
            if freight is not None:
                cursor.execute("""
                    MERGE INTO portal.Freight AS target
                    USING (SELECT ? AS scenario_id, ? AS scenario_name,
                           CAST(? AS DECIMAL(18,2)) AS price_per_container,
                           CAST(? AS DECIMAL(12,3)) AS volume_per_container) AS source
                    ON target.scenario_id = source.scenario_id
                    WHEN MATCHED THEN UPDATE SET
                        scenario_name = source.scenario_name,
                        price_per_container = source.price_per_container,
                        volume_per_container = source.volume_per_container
                    WHEN NOT MATCHED THEN INSERT
                        (scenario_id, scenario_name, price_per_container, volume_per_container)
                        VALUES (source.scenario_id, source.scenario_name,
                                source.price_per_container, source.volume_per_container);
                """, scenario.pk, scenario.name, str(freight.price_per_container), str(freight.volume_per_container))
                export_additional_fields(cursor, 'Freight', freight,
                                         {'price_per_container', 'volume_per_container'}, {'scenario_id': scenario.pk})
                exported_count['freight'] += 1


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
                export_additional_fields(cursor, 'ScenarioPlanSales', plan, {'name', 'flag_order_in_purch'},
                                         {'name': plan.name, 'scenario_name': scenario.name})
                exported_count['plans'] += 1

            # === ЭКСПОРТ ЗАКУПОК И ПЛАТЕЖЕЙ ===
            purchs = list(Purch.objects.all().prefetch_related('purchpay_set__kind_lag_pay'))
            # Remove obsolete settings only from the scenario being exported.
            # Retain the highest SQL ID when old scenario-specific duplicates exist.
            known = {purch.name_key for purch in purchs}
            cursor.execute('SELECT id, name FROM portal.Purch WHERE scenario_name = ? ORDER BY id DESC', scenario.name)
            seen = set()
            for old_id, old_name in cursor.fetchall():
                key = planning_group_key(old_name)
                if key not in known or key in seen:
                    cursor.execute('DELETE FROM portal.PurchPay WHERE purch_id = ?', old_id)
                    cursor.execute('DELETE FROM portal.Purch WHERE id = ? AND scenario_name = ?', old_id, scenario.name)
                else:
                    seen.add(key)

            for purch in purchs:
                # Вставляем/обновляем закупку
                cursor.execute("""
                    MERGE INTO portal.Purch AS target
                    USING (SELECT ? AS name, ? AS lag_income, ? AS scenario_name) AS source
                    ON target.name = source.name AND target.scenario_name = source.scenario_name
                    WHEN MATCHED THEN
                        UPDATE SET name = source.name, lag_income = source.lag_income
                    WHEN NOT MATCHED THEN
                        INSERT (name, lag_income, scenario_name)
                        VALUES (source.name, source.lag_income, source.scenario_name)
                    OUTPUT inserted.id;
                """, purch.name, purch.lag_income, scenario.name)

                row = cursor.fetchone()
                purch_id = row[0] if row else None
                if purch_id is None:
                    raise RuntimeError('SQL Server не вернул ID экспортированной закупки.')

                if purch_id is not None:
                    export_additional_fields(cursor, 'Purch', purch, {'name', 'lag_income', 'name_key'}, {'id': purch_id})
                    # Удаляем старые платежи этой закупки в этом сценарии
                    cursor.execute("""
                        DELETE FROM portal.PurchPay
                        WHERE purch_id = ?
                    """, purch_id)

                    # Вставляем новые платежи
                    for pay in purch.purchpay_set.all():
                        cursor.execute("""
                            INSERT INTO portal.PurchPay (purch_id, name, percent_pay, lag_day_pay, kind_lag_pay_id, kind_lag_pay)
                            OUTPUT inserted.id
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, purch_id, pay.name, pay.percent_pay, pay.lag_day_pay,
                                       pay.kind_lag_pay_id, pay.kind_lag_pay.name if pay.kind_lag_pay else None)
                        pay_row = cursor.fetchone()
                        if not pay_row:
                            raise RuntimeError('SQL Server не вернул ID созданного платежа.')
                        export_additional_fields(cursor, 'PurchPay', pay, {'name', 'percent_pay', 'lag_day_pay'},
                                                 {'id': pay_row[0]})
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
                            ? AS kind_purch,
                            ? AS planning_group_key
                    ) AS source
                    ON target.planning_group_key = source.planning_group_key
                       AND target.scenario_name = source.scenario_name
                    WHEN MATCHED THEN
                        UPDATE SET
                            planning_group = source.planning_group,
                            planning_sales = source.planning_sales,
                            group_goods = source.group_goods,
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
                                percent_stock_end, scenario_name, kind_purch, planning_group_key)
                        VALUES (source.planning_group, source.planning_sales, source.group_goods,
                                source.brand, source.purch, source.volume, source.exw_usd,
                                source.ddp_usd, source.kddp, source.stock_cnt_day,
                                source.percent_stock_end, source.scenario_name, source.kind_purch, source.planning_group_key);
                """,
                               pg.planning_group, pg.planning_sales, pg.group_goods,
                               pg.brand, pg.purch, pg.volume, pg.exw_usd, pg.ddp_usd,
                               pg.kddp, pg.stock_cnt_day, pg.percent_stock_end, scenario.name, pg.kind_purch.name, pg.planning_group_key
                               )
                export_additional_fields(cursor, 'PGGoods', pg, {
                    'planning_group', 'planning_sales', 'group_goods', 'brand', 'purch', 'volume',
                    'exw_usd', 'ddp_usd', 'kddp', 'stock_cnt_day', 'percent_stock_end', 'planning_group_key',
                }, {'planning_group_key': pg.planning_group_key, 'scenario_name': scenario.name})
                exported_count['pggoods'] += 1
                # if pg.planning_group == 'ВЕНТ Канальная':
                #     print('ВЕНТ Канальная')
            conn.commit()
            ScenarioExport.objects.create(scenario=scenario, parameters=parameters, fingerprint=fingerprint(parameters))

        succeeded = True
        total = sum(exported_count.values())
        messages.success(
            request,
            f'✅ Сценарий "{scenario.name}" экспортирован в MS SQL! '
            f'Сценарий: {exported_count["scenario"]}, '
            f'Планов: {exported_count["plans"]}, '
            f'Закупок: {exported_count["purch"]}, '
            f'Платежей: {exported_count["purchpay"]}, '
            f'Товаров: {exported_count["pggoods"]}, '
            f'Фрахт: {exported_count["freight"]}'
        )

    except pyodbc.Error as e:
        logger.error(f"Ошибка экспорта в MS SQL: {e}")
        messages.error(request, f'Сценарий «{scenario.name}»: ошибка экспорта в MS SQL: {e}')
        if conn:
            conn.rollback()
    except Exception as e:
        logger.error(f"Ошибка при экспорте сценария: {e}")
        messages.error(request, f'Сценарий «{scenario.name}»: ошибка при экспорте: {e}')
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return succeeded
