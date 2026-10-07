from .models import Freight
import logging
import json
import pyodbc
from collections import defaultdict
from django.urls import reverse
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.conf import settings
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .models import PGGoods, KindPurch, ScenarioModel, ScenarioPlanSales, Purch, PurchPay, KindLagPay
from .goods_identity import planning_group_key
from .sql_goods import prepare_sql_goods
from .sql_export_fields import ensure_model_columns, ensure_column, export_additional_fields
from .conns import connect_database
from .forms import PGGoodsCopyForm, PGGoodsEditForm, ScenarioModelForm, ScenarioPlanSalesFormSet, ScenarioBulkExportForm

logger = logging.getLogger(__name__)

MS_SQL_CONN_STR = getattr(settings, 'MS_SQL_CONN_STR', None)

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import ResultCalc


from django.shortcuts import render
import logging
import time
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, get_object_or_404
from .models import AlgorithmRun, AlgorithmStep

logger = logging.getLogger(__name__)


# ==============================================================================
# ФУНКЦИИ ШАГОВ АЛГОРИТМА (ЗАГЛУШКИ)
# ==============================================================================

def step_1_start(run_id):
    """Шаг 1: Начало алгоритма"""
    time.sleep(1)
    return {"message": "Алгоритм успешно запущен"}


def execute_algorithm_sql(sql):
    connection, cursor = connect_database('vm-dwh', 'ModelPurch')
    try:
        cursor.execute(sql)
        # Consume every result set so errors in later statements are raised too.
        while cursor.nextset():
            pass
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def step_2_prepare_data(run_id):
    """Prepare corporate calculation data using the configured connection."""
    execute_algorithm_sql("""
        EXEC [ModelPurch].[dbo].[sp_ETLDataBase];
        EXEC [ModelPurch].[dbo].[sp_CreateTableModel];
    """)
    return {"message": "Данные подготовлены"}

from .calc_purch import  calc_purch
def step_3_calculate_order(run_id):
    """Шаг 3: Расчет заказа"""

    calc_purch()
    return {"message": "Заказ рассчитан"}


def step_4_update_tables(run_id):
    execute_algorithm_sql('EXEC [ModelPurch].[dbo].[sp_Date];')
    return {"message": "Таблицы обновлены"}


import subprocess
import os
import tempfile
import logging

logger = logging.getLogger(__name__)


def step_5_olap_cube(run_id):
    execute_algorithm_sql("EXEC msdb.dbo.sp_start_job 'ModelPurch';")
    return {"message": "Куб отправлен на обсчет"}


# Словарь с функциями шагов (порядок -> функция)
STEP_FUNCTIONS = {
    1: step_1_start,
    2: step_2_prepare_data,
    3: step_3_calculate_order,
    4: step_4_update_tables,
    5: step_5_olap_cube,
}


# ==============================================================================
# VIEW ДЛЯ СТРАНИЦЫ РЕЗУЛЬТАТОВ
# ==============================================================================

def results_page(request):
    return render(request, 'model_purch/results.html')


# ==============================================================================
# API: ЗАПУСК АЛГОРИТМА (создает все шаги со статусом "pending")
# ==============================================================================

@csrf_exempt
def start_algorithm_api(request):
    """Запускает новый алгоритм и создает все шаги"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не поддерживается'}, status=405)

    try:
        # Создаем новую сессию алгоритма
        run = AlgorithmRun.objects.create(status='running')

        # Создаем все шаги алгоритма
        steps_config = [
            (1, 'Начало', 'Инициализация алгоритма'),
            (2, 'Подготовка данных', 'Загрузка и валидация исходных данных'),
            (3, 'Расчет заказа', 'Вычисление параметров заказа'),
            (4, 'Обновление таблиц', 'Обновление БД с учетом заказа'),
            (5, 'Обсчет OLAP куба', 'Пересчет аналитического куба'),
        ]

        for order, name, description in steps_config:
            AlgorithmStep.objects.create(
                algorithm_run=run,
                order=order,
                name=name,
                description=description,
                status='pending'
            )

        return JsonResponse({
            'success': True,
            'run_id': run.id,
            'message': f'Алгоритм #{run.id} запущен'
        })

    except Exception as e:
        logger.error(f"Ошибка запуска алгоритма: {e}")
        return JsonResponse({'error': str(e)}, status=500)


# ==============================================================================
# API: ВЫПОЛНЕНИЕ СЛЕДУЮЩЕГО ШАГА
# ==============================================================================

@csrf_exempt
def execute_next_step_api(request, run_id):
    """Выполняет следующий ожидающий шаг алгоритма"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не поддерживается'}, status=405)

    try:
        run = get_object_or_404(AlgorithmRun, pk=run_id)

        # Находим следующий шаг со статусом "pending"
        next_step = AlgorithmStep.objects.filter(
            algorithm_run=run,
            status='pending'
        ).order_by('order').first()

        if not next_step:
            # Все шаги выполнены
            run.status = 'completed'
            run.finished_at = timezone.now()
            run.save()

            return JsonResponse({
                'success': True,
                'finished': True,
                'message': 'Алгоритм полностью выполнен'
            })

        # Обновляем статус на "running"
        next_step.status = 'running'
        next_step.started_at = timezone.now()
        next_step.save()

        # Выполняем функцию шага
        step_func = STEP_FUNCTIONS.get(next_step.order)
        if not step_func:
            raise Exception(f"Функция для шага {next_step.order} не найдена")

        try:
            result = step_func(run_id)

            # Успешное выполнение
            next_step.status = 'completed'
            next_step.finished_at = timezone.now()
            next_step.duration_seconds = (next_step.finished_at - next_step.started_at).total_seconds()
            next_step.result = str(result)
            next_step.save()

            return JsonResponse({
                'success': True,
                'step_id': next_step.id,
                'step_order': next_step.order,
                'step_name': next_step.name,
                'result': result,
                'duration': next_step.duration_seconds
            })

        except Exception as step_error:
            # Ошибка выполнения шага
            next_step.status = 'failed'
            next_step.finished_at = timezone.now()
            next_step.duration_seconds = (next_step.finished_at - next_step.started_at).total_seconds()
            next_step.error_message = str(step_error)
            next_step.save()

            run.status = 'failed'
            run.finished_at = timezone.now()
            run.save()

            return JsonResponse({
                'success': False,
                'error': str(step_error),
                'step_id': next_step.id,
                'step_name': next_step.name
            }, status=500)

    except Exception as e:
        logger.error(f"Ошибка выполнения шага: {e}")
        return JsonResponse({'error': str(e)}, status=500)


# ==============================================================================
# API: ПОЛУЧЕНИЕ СОСТОЯНИЯ АЛГОРИТМА
# ==============================================================================

def get_algorithm_status_api(request, run_id):
    """Возвращает текущее состояние алгоритма и всех его шагов"""
    try:
        run = get_object_or_404(AlgorithmRun, pk=run_id)
        steps = AlgorithmStep.objects.filter(algorithm_run=run).order_by('order')

        steps_data = []
        for step in steps:
            steps_data.append({
                'id': step.id,
                'order': step.order,
                'name': step.name,
                'description': step.description,
                'status': step.status,
                'started_at': step.started_at.isoformat() if step.started_at else None,
                'finished_at': step.finished_at.isoformat() if step.finished_at else None,
                'duration_seconds': step.duration_seconds,
                'result': step.result,
                'error_message': step.error_message,
            })

        return JsonResponse({
            'run_id': run.id,
            'status': run.status,
            'started_at': run.started_at.isoformat(),
            'finished_at': run.finished_at.isoformat() if run.finished_at else None,
            'steps': steps_data,
            'progress': {
                'total': len(steps_data),
                'completed': sum(1 for s in steps_data if s['status'] == 'completed'),
                'failed': sum(1 for s in steps_data if s['status'] == 'failed'),
                'pending': sum(1 for s in steps_data if s['status'] == 'pending'),
            }
        })

    except Exception as e:
        logger.error(f"Ошибка получения статуса: {e}")
        return JsonResponse({'error': str(e)}, status=500)

def export_scenario_to_sql(request, pk):
    scenario = get_object_or_404(ScenarioModel, pk=pk)
    _export_scenario_to_sql(request, scenario)
    return redirect('scenario_list')


@require_POST
def bulk_export_scenarios(request):
    form = ScenarioBulkExportForm(request.POST)
    if not form.is_valid():
        messages.error(request, ' '.join(str(error) for errors in form.errors.values() for error in errors))
        return redirect('scenario_list')
    if not MS_SQL_CONN_STR:
        messages.error(request, 'Не настроено подключение к MS SQL Server')
        return redirect('scenario_list')
    scenarios = list(form.cleaned_data['scenarios'].order_by('pk'))
    succeeded = sum(_export_scenario_to_sql(request, scenario) for scenario in scenarios)
    summary = f'Массовый экспорт завершён. Успешно: {succeeded} из {len(scenarios)}. Ошибок: {len(scenarios) - succeeded}.'
    if succeeded == len(scenarios):
        messages.success(request, summary)
    else:
        messages.warning(request, summary)
    return redirect('scenario_list')


def _export_scenario_to_sql(request, scenario):
    """
    Экспортирует данные сценария в MS SQL Server (схема portal).
    Автоматически создаёт таблицы, если они не существуют.
    """

    if not MS_SQL_CONN_STR:
        messages.error(request, "Не настроено подключение к MS SQL Server")
        return False

    conn = None
    succeeded = False
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
            ('Purch', Purch, {'scenario_plan'}),
            ('PurchPay', PurchPay, {'purch', 'kind_lag_pay'}),
            ('PGGoods', PGGoods, {'scenario_plan', 'kind_purch'}),
            ('KindLagPay', KindLagPay, set()),
        ]:
            ensure_model_columns(cursor, table, model, relations)
        ensure_column(cursor, 'PurchPay', 'kind_lag_pay_id', 'INT')
        ensure_column(cursor, 'PurchPay', 'kind_lag_pay', 'NVARCHAR(255)')
        conn.commit()

        exported_count = {'scenario': 0, 'purch': 0, 'purchpay': 0, 'pggoods': 0, 'plans': 0, 'freight': 0}

        with transaction.atomic():
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
            purchs = Purch.objects.filter(scenario_plan=scenario).prefetch_related('purchpay_set__kind_lag_pay')

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
                if purch_id is None:
                    raise RuntimeError('SQL Server не вернул ID экспортированной закупки.')

                if purch_id is not None:
                    export_additional_fields(cursor, 'Purch', purch, {'name', 'lag_income'}, {'id': purch_id})
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
# ==============================================================================
# ФУНКЦИИ КОПИРОВАНИЯ ДАННЫХ МЕЖДУ СЦЕНАРИЯМИ
# ==============================================================================

def copy_purch_from_scenario(request):
    """Копирует закупки и графики платежей из выбранного сценария в текущий"""
    if request.method == 'POST':
        form = PGGoodsCopyForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            target = form.cleaned_data.get('scenario')
            return redirect(f"{reverse('purch_list')}?scenario={target.pk}" if target else reverse('purch_list'))
        current_scenario = form.cleaned_data['scenario']
        source_scenario = form.cleaned_data['source_scenario_id']

        source_purchs = Purch.objects.filter(scenario_plan=source_scenario).prefetch_related('purchpay_set')
        copied_count = 0

        with transaction.atomic():
            for sp in source_purchs:
                # Ищем или создаем закупку с таким же именем в целевом сценарии
                p, created = Purch.objects.get_or_create(
                    name=sp.name,
                    scenario_plan=current_scenario,
                    defaults={'lag_income': sp.lag_income, 'lage_make': sp.lage_make}
                )
                # Обновляем параметры закупки на случай изменений в источнике
                if p.lag_income != sp.lag_income or p.lage_make != sp.lage_make:
                    p.lag_income = sp.lag_income
                    p.lage_make = sp.lage_make
                    p.save()

                # Полностью заменяем платежи на те, что в источнике
                p.purchpay_set.all().delete()
                new_pays = [
                    PurchPay(purch=p, name=pay.name, percent_pay=pay.percent_pay, lag_day_pay=pay.lag_day_pay,
                             kind_lag_pay_id=pay.kind_lag_pay_id)
                    for pay in sp.purchpay_set.all()
                ]
                if new_pays:
                    PurchPay.objects.bulk_create(new_pays)

                copied_count += 1

        messages.success(request,
                         f'✅ Успешно скопировано/обновлено {copied_count} закупок из сценария "{source_scenario.name}".')
        return redirect(f"{reverse('purch_list')}?scenario={current_scenario.id}")

    return redirect('purch_list')


def copy_pggoods_from_scenario(request):
    """Copy goods into the target explicitly submitted by the copy form."""
    if request.method == 'POST':
        form = PGGoodsCopyForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            target = form.cleaned_data.get('scenario')
            if target:
                return redirect(f"{reverse('pggoods_list')}?scenario={target.pk}")
            return redirect('pggoods_list')

        current_scenario = form.cleaned_data['scenario']
        source_scenario = form.cleaned_data['source_scenario_id']

        source_goods = PGGoods.objects.filter(scenario_plan=source_scenario).select_related('kind_purch')
        created_count = 0
        updated_count = 0

        with transaction.atomic():
            for sg in source_goods:
                # update_or_create предотвращает дубликаты: если товар с такими же плановыми группами уже есть, он обновится
                obj, created = PGGoods.objects.update_or_create(
                    scenario_plan=current_scenario,
                    planning_group_key=planning_group_key(sg.planning_group),
                    defaults={
                        'planning_group': sg.planning_group,
                        'planning_sales': sg.planning_sales,
                        'group_goods': sg.group_goods,
                        'brand': sg.brand,
                        'purch': sg.purch,
                        'kind_purch': sg.kind_purch,
                        'volume': sg.volume,
                        'exw_usd': sg.exw_usd,
                        'ddp_usd': sg.ddp_usd,
                        'kddp': sg.kddp,
                        'stock_cnt_day': sg.stock_cnt_day,
                        'percent_stock_end': sg.percent_stock_end,
                    }
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        messages.success(request,
                         f'✅ Обработано товаров из "{source_scenario.name}": создано {created_count}, обновлено {updated_count}.')
        return redirect(f"{reverse('pggoods_list')}?scenario={current_scenario.id}")

    return redirect('pggoods_list')

# ==============================================================================
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: получение текущего сценария из GET-параметров
# ==============================================================================
# ==============================================================================
# 1. СИНХРОНИЗАЦИЯ ДАННЫХ ИЗ MS SQL
# ==============================================================================

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
    plans_obj = ScenarioPlanSales.objects.filter(scenario_model = scenario_obj)

    str_scenarios = ", ".join([f"'{p.name}'" for p in plans_obj])
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
                    'planning_group': planning_group, 'planning_sales': planning_sales, 'group_goods': group_goods,
                    'brand': brand, 'purch': purch_name, 'kind_purch': kind_purch_obj,
                    'volume': volume, 'exw_usd': 0.0, 'ddp_usd': ddp_usd,
                    'kddp': 1.0 if ddp_usd > 0 else 0.0, 'stock_cnt_day': 0, 'percent_stock_end': 0.0,
                }

                if overwrite:
                    # РЕЖИМ ПЕРЕЗАПИСИ: update_or_create — обновляет существующие
                    # PGGoods.objects.filter(scenario_plan=scenario).delete()
                    pggoods, created = PGGoods.objects.update_or_create(
                        scenario_plan=scenario, planning_group_key=planning_group_key(planning_group),
                        defaults=defaults
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                else:
                    # РЕЖИМ БЕЗ ПЕРЕЗАПИСИ: только создаём новые
                    pggoods, created = PGGoods.objects.get_or_create(
                        scenario_plan=scenario, planning_group_key=planning_group_key(planning_group),
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
# ==============================================================================
# 2. VIEW ДЛЯ СЦЕНАРИЕВ
# ==============================================================================

def scenario_list(request):
    scenarios = ScenarioModel.objects.all().order_by('-date_start_plan')
    return render(request, 'model_purch/scenario_list.html', {'scenarios': scenarios})


def scenario_create(request):
    if request.method == 'POST':
        scenario_form = ScenarioModelForm(request.POST)
        plan_formset = ScenarioPlanSalesFormSet(request.POST, prefix='plans')

        if scenario_form.is_valid() and plan_formset.is_valid():
            scenario = scenario_form.save()
            plan_formset.instance = scenario
            plan_formset.save()

            try:
                sync_purch_data_for_scenario(scenario)
                sync_pggoods_data_for_scenario(scenario)
                messages.success(request, f'Сценарий "{scenario.name}" создан, данные синхронизированы!')
            except Exception as e:
                logger.error(f"Ошибка синхронизации: {e}")
                messages.warning(request, f'Сценарий создан, но ошибка загрузки данных MS SQL: {e}')
            return redirect('scenario_list')
    else:
        scenario_form = ScenarioModelForm()
        plan_formset = ScenarioPlanSalesFormSet(prefix='plans')

    return render(request, 'model_purch/scenario_form.html', {
        'scenario_form': scenario_form, 'plan_formset': plan_formset, 'title': 'Создание сценария'
    })


def scenario_edit(request, pk):
    scenario = get_object_or_404(ScenarioModel, pk=pk)
    if request.method == 'POST':
        scenario_form = ScenarioModelForm(request.POST, instance=scenario)
        plan_formset = ScenarioPlanSalesFormSet(request.POST, instance=scenario, prefix='plans')

        if scenario_form.is_valid() and plan_formset.is_valid():
            scenario_form.save()
            plan_formset.save()

            try:
                sync_purch_data_for_scenario(scenario)
                sync_pggoods_data_for_scenario(scenario)
                messages.success(request, f'Сценарий "{scenario.name}" обновлён, данные синхронизированы!')
            except Exception as e:
                logger.error(f"Ошибка синхронизации: {e}")
                messages.warning(request, f'Сценарий обновлён, но ошибка загрузки данных MS SQL: {e}')
            return redirect('scenario_list')
    else:
        scenario_form = ScenarioModelForm(instance=scenario)
        plan_formset = ScenarioPlanSalesFormSet(instance=scenario, prefix='plans')

    return render(request, 'model_purch/scenario_form.html', {
        'scenario_form': scenario_form, 'plan_formset': plan_formset,
        'title': f'Редактирование: {scenario.name}', 'scenario': scenario
    })


def scenario_delete(request, pk):
    scenario = get_object_or_404(ScenarioModel, pk=pk)
    if request.method == 'POST':
        scenario_name = scenario.name
        scenario.delete()  # CASCADE удалит связанные Purch и PGGoods
        messages.success(request, f'Сценарий "{scenario_name}" удалён!')
        return redirect('scenario_list')
    return render(request, 'model_purch/scenario_confirm_delete.html', {'scenario': scenario})


# ==============================================================================
# 3. VIEW ДЛЯ PGGoods (ПОЛНОСТЬЮ С УЧЁТОМ scenario_plan)
# ==============================================================================

def pggoods_list(request):
    """
    Список PGGoods с inline-редактированием.
    ВАЖНО: все операции фильтруются по current_scenario.
    """
    current_scenario, all_scenarios = get_current_scenario(request)

    # Базовый queryset — только записи текущего сценария
    if current_scenario:
        queryset = PGGoods.objects.filter(scenario_plan=current_scenario).select_related('kind_purch')
    else:
        queryset = PGGoods.objects.none()

    # --- Обработка inline-редактирования (POST) ---
    if request.method == 'POST':
        record_id = request.POST.get('record_id')
        if record_id:
            # ВАЖНО: ищем запись ТОЛЬКО в рамках текущего сценария
            goods = get_object_or_404(PGGoods, pk=record_id, scenario_plan=current_scenario)
            form = PGGoodsEditForm(request.POST, instance=goods)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        form.save()
                    messages.success(request, f'✅ Запись «{goods.group_goods}» обновлена.')
                    logger.info(f"Inline-обновление PGGoods ID={record_id} в сценарии '{current_scenario.name}'")
                except Exception as e:
                    logger.error(f"Ошибка inline-сохранения ID={record_id}: {e}")
                    messages.error(request, f'❌ Ошибка сохранения: {e}')
            else:
                messages.error(request, f'❌ Ошибки в форме: {form.errors.as_text()}')
        else:
            messages.error(request, 'Не указан ID записи.')
        query_string = request.POST.get('query_string', '')
        return redirect(f'/model_purch/?{query_string}' if query_string else '/model_purch/')

    # --- Фильтры (работают только в рамках текущего сценария) ---
    search = request.GET.get('search', '').strip()
    if search:
        queryset = queryset.filter(
            Q(group_goods__icontains=search) |
            Q(planning_group__icontains=search) |
            Q(planning_sales__icontains=search) |
            Q(brand__icontains=search) |
            Q(purch__icontains=search)
        )

    planning_sales = request.GET.get('planning_sales')
    if planning_sales:
        queryset = queryset.filter(planning_sales=planning_sales)

    planning_group = request.GET.get('planning_group')
    if planning_group:
        queryset = queryset.filter(planning_group=planning_group)

    kind_purch_id = request.GET.get('kind_purch')
    if kind_purch_id:
        queryset = queryset.filter(kind_purch_id=kind_purch_id)

    group_goods = request.GET.get('group_goods')
    if group_goods:
        queryset = queryset.filter(group_goods=group_goods)

    brand = request.GET.get('brand')
    if brand:
        queryset = queryset.filter(brand=brand)

    purch = request.GET.get('purch')
    if purch:
        queryset = queryset.filter(purch=purch)

    # --- Сортировка ---
    sort_by = request.GET.get('sort', None)
    sort_dir = request.GET.get('dir', 'asc')
    allowed_sort_fields = {
        'id', 'planning_group', 'planning_sales', 'group_goods',
        'brand', 'purch', 'volume', 'exw_usd', 'ddp_usd', 'kddp',
        'stock_cnt_day', 'percent_stock_end',
    }

    if sort_by is None:
        queryset = queryset.order_by('planning_sales', 'group_goods', 'brand')
    elif sort_by in allowed_sort_fields:
        order_field = f'-{sort_by}' if sort_dir == 'desc' else sort_by
        queryset = queryset.order_by(order_field)
    else:
        queryset = queryset.order_by('planning_sales', 'group_goods', 'brand')

    # --- Пагинация ---
    try:
        page_number = int(request.GET.get('page', 1))
        if page_number < 1: page_number = 1
    except (ValueError, TypeError):
        page_number = 1

    paginator = Paginator(queryset, 25)
    page_obj = paginator.get_page(page_number)

    # --- Уникальные значения для фильтров (только для текущего сценария!) ---
    planning_sales_list = queryset.exclude(planning_sales__isnull=True).exclude(planning_sales='').values_list(
        'planning_sales', flat=True).distinct().order_by('planning_sales')
    planning_groups_list = queryset.exclude(planning_group__isnull=True).exclude(planning_group='').values_list(
        'planning_group', flat=True).distinct().order_by('planning_group')
    group_goods_list = queryset.exclude(group_goods__isnull=True).exclude(group_goods='').values_list('group_goods',
                                                                                                      flat=True).distinct().order_by(
        'group_goods')
    brand_list = queryset.exclude(brand__isnull=True).exclude(brand='').values_list('brand',
                                                                                    flat=True).distinct().order_by(
        'brand')
    purch_list = queryset.exclude(purch__isnull=True).exclude(purch='').values_list('purch',
                                                                                    flat=True).distinct().order_by(
        'purch')
    kind_purch_list = KindPurch.objects.all().order_by('name')

    get_params = request.GET.copy()
    get_params.pop('page', None)
    clean_query_string = get_params.urlencode()

    return render(request, 'model_purch/pggoods_list.html', {
        'page_obj': page_obj,
        'search': search,
        'planning_sales': planning_sales,
        'planning_group': planning_group,
        'kind_purch_id': kind_purch_id,
        'group_goods': group_goods,
        'brand': brand,
        'purch': purch,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'planning_sales_list': planning_sales_list,
        'planning_groups_list': planning_groups_list,
        'kind_purch_list': kind_purch_list,
        'group_goods_list': group_goods_list,
        'brand_list': brand_list,
        'purch_list': purch_list,
        'title': 'Справочник товаров в группах планирования',
        'query_string': request.GET.urlencode(),
        'clean_query_string': clean_query_string,
        'scenarios': all_scenarios,
        'current_scenario': current_scenario,
    })


def edit_pggoods(request, pk):
    """
    Редактирование PGGoods.
    ВАЖНО: проверяем, что запись принадлежит текущему сценарию.
    """
    current_scenario, _ = get_current_scenario(request)
    # Ищем запись ТОЛЬКО в рамках текущего сценария
    goods = get_object_or_404(PGGoods, pk=pk, scenario_plan=current_scenario)

    if request.method == 'POST':
        form = PGGoodsEditForm(request.POST, instance=goods)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                messages.success(request, f'Данные для «{goods.group_goods}» обновлены.')
                return redirect(f"{request.META.get('HTTP_REFERER', 'pggoods_list')}")
            except Exception as e:
                logger.error(f"Ошибка сохранения PGGoods ID={pk}: {e}")
                messages.error(request, f'Ошибка при сохранении: {e}')
        else:
            messages.error(request, 'Пожалуйста, исправьте ошибки в форме.')
    else:
        form = PGGoodsEditForm(instance=goods)

    return render(request, 'model_purch/edit_pggoods.html', {
        'form': form, 'goods': goods, 'title': f'Редактирование: {goods.group_goods}',
        'current_scenario': current_scenario,
    })


def delete_pggoods(request, pk):
    """
    Удаление PGGoods.
    ВАЖНО: удаляем только если запись принадлежит текущему сценарию.
    """
    current_scenario, _ = get_current_scenario(request)
    goods = get_object_or_404(PGGoods, pk=pk, scenario_plan=current_scenario)

    if request.method == 'POST':
        name = goods.group_goods
        goods.delete()
        messages.success(request, f'Запись «{name}» удалена из сценария "{current_scenario.name}".')
        logger.info(f"Пользователь {request.user} удалил PGGoods ID={pk} из сценария '{current_scenario.name}'")
    return redirect('pggoods_list')


@require_POST
def bulk_update_pggoods(request):
    """
    Массовое обновление PGGoods.
    ВАЖНО: обновляем только записи текущего сценария.
    """
    current_scenario, _ = get_current_scenario(request)

    try:
        data = json.loads(request.body)
        updates = data.get('updates', [])

        if not updates:
            return JsonResponse({'success': False, 'message': 'Нет данных для обновления'}, status=400)

        updated_count = 0
        errors = []

        with transaction.atomic():
            for item_data in updates:
                record_id = item_data.get('id')
                if not record_id:
                    continue

                try:
                    # ВАЖНО: ищем запись ТОЛЬКО в рамках текущего сценария
                    goods = PGGoods.objects.get(pk=record_id, scenario_plan=current_scenario)

                    if 'group_goods' in item_data:
                        goods.group_goods = item_data['group_goods']
                    if 'brand' in item_data:
                        goods.brand = item_data['brand']
                    if 'purch' in item_data:
                        goods.purch = item_data['purch']
                    if 'volume' in item_data:
                        goods.volume = float(item_data['volume'])
                    if 'exw_usd' in item_data:
                        goods.exw_usd = float(item_data['exw_usd'])
                    if 'ddp_usd' in item_data:
                        goods.ddp_usd = float(item_data['ddp_usd'])
                    if 'stock_cnt_day' in item_data:
                        goods.stock_cnt_day = int(item_data['stock_cnt_day'])
                    if 'percent_stock_end' in item_data:
                        goods.percent_stock_end = float(item_data['percent_stock_end'])

                    if 'kind_purch' in item_data:
                        kind_purch_id = item_data['kind_purch']
                        if kind_purch_id:
                            try:
                                goods.kind_purch = KindPurch.objects.get(pk=int(kind_purch_id))
                            except KindPurch.DoesNotExist:
                                errors.append(f'Запись ID={record_id}: Вид закупки не найден')
                        else:
                            goods.kind_purch = None

                    if goods.exw_usd and goods.exw_usd > 0 and goods.ddp_usd is not None:
                        goods.kddp = goods.ddp_usd / goods.exw_usd

                    goods.save()
                    updated_count += 1

                except PGGoods.DoesNotExist:
                    errors.append(f'Запись ID={record_id} не найдена в сценарии "{current_scenario.name}"')
                except Exception as e:
                    errors.append(f'Запись ID={record_id}: {str(e)}')

        if errors:
            return JsonResponse({
                'success': True, 'updated': updated_count, 'errors': errors,
                'message': f'Обновлено: {updated_count}, Ошибок: {len(errors)}'
            })

        return JsonResponse({
            'success': True, 'updated': updated_count,
            'message': f'✅ Успешно обновлено записей: {updated_count} в сценарии "{current_scenario.name}"'
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Неверный формат данных'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Ошибка сервера: {str(e)}'}, status=500)


def export_to_excel(request):
    """
    Экспорт PGGoods в Excel.
    ВАЖНО: экспортируем только записи текущего сценария.
    """
    current_scenario, _ = get_current_scenario(request)

    if current_scenario:
        queryset = PGGoods.objects.filter(scenario_plan=current_scenario).select_related('kind_purch')
    else:
        queryset = PGGoods.objects.none()

    # Применяем те же фильтры, что и в списке
    search = request.GET.get('search', '').strip()
    if search:
        queryset = queryset.filter(
            Q(group_goods__icontains=search) |
            Q(planning_group__icontains=search) |
            Q(planning_sales__icontains=search) |
            Q(brand__icontains=search) |
            Q(purch__icontains=search)
        )

    for field in ['planning_sales', 'planning_group', 'group_goods', 'brand', 'purch']:
        val = request.GET.get(field)
        if val:
            queryset = queryset.filter(**{field: val})

    kind_purch_id = request.GET.get('kind_purch')
    if kind_purch_id:
        queryset = queryset.filter(kind_purch_id=kind_purch_id)

    wb = Workbook()
    ws = wb.active
    ws.title = f"PGGoods - {current_scenario.name if current_scenario else 'All'}"

    header_font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    headers = [
        'ID', 'Сценарий', 'План. группа', 'План. продажи', 'Группа товаров',
        'Бренд', 'Закупка', 'Вид закупки', 'Объём',
        'EXW USD', 'DDP USD', 'KDDP', 'Запас (дни)', '% запаса'
    ]

    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    column_widths = [8, 25, 20, 20, 30, 20, 20, 20, 12, 12, 12, 12, 12, 12]
    for col_num, width in enumerate(column_widths, 1):
        ws.column_dimensions[get_column_letter(col_num)].width = width

    for row_num, item in enumerate(queryset, 2):
        data = [
            item.id,
            item.scenario_plan.name if item.scenario_plan else '',
            item.planning_group,
            item.planning_sales,
            item.group_goods,
            item.brand or '',
            item.purch or '',
            str(item.kind_purch) if item.kind_purch else '',
            item.volume,
            item.exw_usd,
            item.ddp_usd,
            item.kddp,
            item.stock_cnt_day,
            item.percent_stock_end,
        ]

        for col_num, value in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col_num, value=value)
            cell.border = thin_border
            if col_num in [9, 10, 11, 12, 13, 14]:
                cell.alignment = Alignment(horizontal='right')
            else:
                cell.alignment = Alignment(horizontal='left')

    ws.freeze_panes = 'A2'

    scenario_name = current_scenario.name if current_scenario else 'all'
    safe_name = "".join(c for c in scenario_name if c.isalnum() or c in (' ', '_')).rstrip()
    filename = f"pggoods_export_{safe_name}.xlsx"

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)

    logger.info(f"Экспорт в Excel: {queryset.count()} записей из сценария '{scenario_name}'")
    return response


def import_from_excel(request):
    """
    Импорт данных из Excel в PGGoods.
    ВАЖНО: импортируем в текущий сценарий.
    """
    current_scenario, _ = get_current_scenario(request)

    if not current_scenario:
        messages.error(request, '❌ Сначала выберите или создайте сценарий.')
        return redirect('pggoods_list')

    if request.method == 'POST':
        excel_file = request.FILES.get('excel_file')

        if not excel_file:
            messages.error(request, '📁 Файл не выбран.')
            return redirect(f"{reverse('pggoods_list')}?scenario={current_scenario.id}")

        if not excel_file.name.endswith('.xlsx'):
            messages.error(request, '❌ Поддерживается только формат .xlsx')
            return redirect(f"{reverse('pggoods_list')}?scenario={current_scenario.id}")

        try:
            from openpyxl import load_workbook
            wb = load_workbook(excel_file, read_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            wb.close()

            created_count = 0
            updated_count = 0
            error_count = 0
            errors = []

            with transaction.atomic():
                for row_num, row in enumerate(rows, 2):
                    try:
                        if len(row) < 14:
                            errors.append(f"Строка {row_num}: недостаточно колонок")
                            error_count += 1
                            continue

                        (
                            item_id, scenario_col, planning_group, planning_sales, group_goods,
                            brand, purch, kind_purch_name, volume,
                            exw_usd, ddp_usd, kddp, stock_cnt_day, percent_stock_end
                        ) = row

                        if not planning_group and not planning_sales and not group_goods:
                            continue

                        if not planning_group or not str(planning_group).strip():
                            raise ValueError('Не заполнена плановая группа')
                        group_name = str(planning_group).strip()
                        identity = planning_group_key(group_name)
                        kind_purch = None
                        if kind_purch_name:
                            kind_purch, _ = KindPurch.objects.get_or_create(name=str(kind_purch_name).strip())

                        # A savepoint keeps one invalid row from breaking the entire workbook.
                        with transaction.atomic():
                            goods = None
                            if item_id:
                                goods = PGGoods.objects.filter(pk=int(item_id), scenario_plan=current_scenario).first()
                            if goods and PGGoods.objects.filter(
                                scenario_plan=current_scenario, planning_group_key=identity,
                            ).exclude(pk=goods.pk).exists():
                                raise ValueError('Плановая группа уже существует в этом сценарии с другим ID')
                            if kind_purch is None:
                                existing = goods or PGGoods.objects.filter(
                                    scenario_plan=current_scenario, planning_group_key=identity,
                                ).first()
                                kind_purch = existing.kind_purch if existing else KindPurch.objects.get_or_create(name='Закупается')[0]
                            defaults = {
                                'planning_group': group_name,
                                'planning_sales': str(planning_sales).strip() if planning_sales else '',
                                'group_goods': str(group_goods).strip() if group_goods else '',
                                'brand': str(brand).strip() if brand else None,
                                'purch': str(purch).strip() if purch else None,
                                'kind_purch': kind_purch,
                                'volume': float(volume) if volume else 0.0,
                                'exw_usd': float(exw_usd) if exw_usd else 0.0,
                                'ddp_usd': float(ddp_usd) if ddp_usd else 0.0,
                                'kddp': float(kddp) if kddp else 0.0,
                                'stock_cnt_day': int(stock_cnt_day) if stock_cnt_day else 0,
                                'percent_stock_end': float(percent_stock_end) if percent_stock_end else 0.0,
                            }
                            if goods:
                                for key, value in defaults.items():
                                    setattr(goods, key, value)
                                goods.save()
                                created = False
                            else:
                                goods, created = PGGoods.objects.update_or_create(
                                    scenario_plan=current_scenario, planning_group_key=identity, defaults=defaults,
                                )
                            if created:
                                created_count += 1
                            else:
                                updated_count += 1

                    except Exception as e:
                        error_count += 1
                        errors.append(f"Строка {row_num}: {str(e)}")
                        logger.error(f"Ошибка импорта строки {row_num}: {e}")

            result_msg = f'✅ Импорт в сценарий "{current_scenario.name}" завершён. Создано: {created_count}, Обновлено: {updated_count}'
            if error_count > 0:
                result_msg += f', Ошибок: {error_count}'
                for error in errors[:5]:
                    messages.warning(request, f'⚠️ {error}')

            messages.success(request, result_msg)
            logger.info(
                f"Импорт в сценарий '{current_scenario.name}': создано={created_count}, обновлено={updated_count}")

        except Exception as e:
            logger.error(f"Ошибка при обработке Excel файла: {e}")
            messages.error(request, f'❌ Ошибка при обработке файла: {e}')

        # ✅ ИСПРАВЛЕНО: сохраняем scenario в редиректе

        return redirect(f"{reverse('pggoods_list')}?scenario={current_scenario.id}")

    return render(request, 'model_purch/import_excel.html', {
        'current_scenario': current_scenario,
    })

# ==============================================================================
# 4. VIEW ДЛЯ Purch (ПОЛНОСТЬЮ С УЧЁТОМ scenario_plan)
# ==============================================================================

def purch_list(request):
    current_scenario, all_scenarios = get_current_scenario(request)

    if current_scenario:
        purch_queryset = Purch.objects.filter(scenario_plan=current_scenario).order_by('name')
    else:
        purch_queryset = Purch.objects.none()

    return render(request, 'model_purch/purch_list.html', {
        'purch_list': purch_queryset, 'scenarios': all_scenarios,
        'current_scenario': current_scenario, 'title': 'Настройки закупок (Purch)'
    })


from .forms import PurchForm, PurchPayFormSet  # Убедитесь, что PurchPayFormSet импортирован!


def get_current_scenario(request):
    all_scenarios = ScenarioModel.objects.all().order_by('-date_start_plan', '-id')
    scenario_id = request.GET.get('scenario')
    if scenario_id:
        try:
            current_scenario = ScenarioModel.objects.get(pk=scenario_id)
        except (ScenarioModel.DoesNotExist, ValueError, TypeError):
            current_scenario = all_scenarios.first()
    else:
        current_scenario = all_scenarios.first()
    return current_scenario, all_scenarios


def purch_create(request):
    current_scenario, _ = get_current_scenario(request)

    if not current_scenario:
        messages.error(request, '❌ Сначала создайте сценарий.')
        return redirect('scenario_list')

    if request.method == 'POST':
        form = PurchForm(request.POST)
        # ВАЖНО: передаем request.POST в formset, но пока без instance (он будет назначен после сохранения Purch)
        formset = PurchPayFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            purch = form.save(commit=False)
            purch.scenario_plan = current_scenario
            purch.save()

            # Теперь привязываем formset к сохраненному purch и сохраняем платежи
            formset.instance = purch
            formset.save()

            messages.success(request, f'Закупка "{purch.name}" создана для сценария "{current_scenario.name}"')
            return redirect(f"{reverse('purch_list')}?scenario={current_scenario.id}")
    else:
        form = PurchForm()
        formset = PurchPayFormSet()  # Пустой formset для создания

    return render(request, 'model_purch/purch_edit.html', {
        'form': form,
        'formset': formset,  # ОБЯЗАТЕЛЬНО передаем в шаблон
        'scenario': current_scenario,
        'title': f'Создание закупки для: {current_scenario.name}'
    })


def purch_edit(request, pk=None):
    """Создание или редактирование закупки"""
    purch = get_object_or_404(Purch, pk=pk) if pk else None

    # Получаем сценарий из URL-параметра
    scenario_id = request.GET.get('scenario') or (purch.scenario_plan_id if purch else None)
    scenario = get_object_or_404(ScenarioModel, pk=scenario_id) if scenario_id else None

    if request.method == 'POST':
        form = PurchForm(request.POST, instance=purch)
        formset = PurchPayFormSet(request.POST, instance=purch)

        if form.is_valid() and formset.is_valid():
            new_purch = form.save(commit=False)
            # Устанавливаем сценарий программно (не из формы)
            if scenario:
                new_purch.scenario_plan = scenario
            new_purch.save()
            formset.save()
            return redirect(f"{reverse('purch_list')}?scenario={scenario.pk}" if scenario else reverse('purch_list'))
    else:
        form = PurchForm(instance=purch)
        formset = PurchPayFormSet(instance=purch)

    return render(request, 'model_purch/purch_edit.html', {
        'form': form,
        'formset': formset,
        'scenario': scenario,
        'title': f'Редактирование: {purch.name}' if purch else 'Новая закупка',
    })

def purch_delete(request, pk):
    """
    Удаление Purch.
    ВАЖНО: удаляем только если запись принадлежит текущему сценарию.
    """
    current_scenario, _ = get_current_scenario(request)
    purch = get_object_or_404(Purch, pk=pk, scenario_plan=current_scenario)

    if request.method == 'POST':
        name = purch.name
        purch.delete()
        messages.success(request, f'Закупка «{name}» удалена из сценария "{current_scenario.name}".')
    return redirect(f"{reverse('purch_list')}?scenario={current_scenario.pk}")
