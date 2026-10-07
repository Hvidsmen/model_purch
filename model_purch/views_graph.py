import pyodbc
import logging
from django.conf import settings
from django.shortcuts import render
from django.http import JsonResponse

logger = logging.getLogger(__name__)
MS_SQL_CONN_STR = getattr(settings, 'MS_SQL_CONN_STR', None)

# Жестко заданная группа планирования
TARGET_PLANNING_GROUP = "Kent Spl (o/f-0) (Kumo) [MRAC]"


def stock_chart_page(request):
    """Отображает страницу с графиком"""
    return render(request, 'model_purch/stock_chart.html')


def stock_chart_data_api(request):
    """API: Возвращает JSON данные для графика из двух таблиц с фильтром по PlanningGroupOZP"""
    if not MS_SQL_CONN_STR:
        return JsonResponse({'error': 'Не настроено подключение к БД'}, status=500)

    group_goods = request.GET.get('group_goods', '').strip()
    planning_sales = request.GET.get('planning_sales', '').strip()

    conn = None
    try:
        conn = pyodbc.connect(MS_SQL_CONN_STR)
        cursor = conn.cursor()

        # 1. Получаем опции для фильтров (только для нужной PlanningGroupOZP)
        def get_distinct_from_stock(column):
            cursor.execute(
                f"SELECT DISTINCT [{column}] FROM [ModelPurch].[dbo].[vStockOnDatePlan] "
                f"WHERE [{column}] IS NOT NULL AND [{column}] != '' "
                f"AND [PlanningGroupOZP] = ? "
                f"ORDER BY [{column}]",
                [TARGET_PLANNING_GROUP]
            )
            return [row[0] for row in cursor.fetchall()]

        filters = {
            'group_goods': get_distinct_from_stock('group_goods'),
            'planning_sales': get_distinct_from_stock('planning_sales'),
        }

        # 2. Запрос к vStockOnDatePlan (Остатки)
        sql_stock = """
            SELECT 
                [Date_],
                SUM([Qty]) AS TotalQty,
                SUM([volume]) AS TotalVolume,
                SUM([exw_usd]) AS TotalExw,
                SUM([ddp_usd]) AS TotalDdp
            FROM [ModelPurch].[dbo].[vStockOnDatePlan]
            WHERE [PlanningGroupOZP] = ?
        """
        params_stock = [TARGET_PLANNING_GROUP]

        if group_goods:
            sql_stock += " AND group_goods = ?"
            params_stock.append(group_goods)
        if planning_sales:
            sql_stock += " AND planning_sales = ?"
            params_stock.append(planning_sales)

        sql_stock += " GROUP BY [Date_] ORDER BY [Date_] ASC"

        cursor.execute(sql_stock, params_stock)
        columns = [col[0] for col in cursor.description]
        rows_stock = [dict(zip(columns, row)) for row in cursor.fetchall()]

        # 3. Запрос к vIncomesPlan (Поступления)
        sql_incomes = """
            SELECT 
                [Date_],
                SUM([Qty]) AS TotalQty,
                SUM([Volume]) AS TotalVolume,
                SUM([exw_usd]) AS TotalExw,
                SUM([ddp_usd]) AS TotalDdp
            FROM [ModelPurch].[dbo].[vIncomesPlan]
            WHERE [PlanningGroupOZP] = ?
        """
        params_incomes = [TARGET_PLANNING_GROUP]

        if group_goods:
            sql_incomes += " AND group_goods = ?"
            params_incomes.append(group_goods)
        if planning_sales:
            sql_incomes += " AND Type_ = ?"
            params_incomes.append(planning_sales)

        sql_incomes += " GROUP BY [Date_] ORDER BY [Date_] ASC"

        cursor.execute(sql_incomes, params_incomes)
        columns = [col[0] for col in cursor.description]
        rows_incomes = [dict(zip(columns, row)) for row in cursor.fetchall()]

        # 4. Форматируем даты и числа для обоих наборов
        def format_rows(rows):
            for row in rows:
                if row['Date_']:
                    if hasattr(row['Date_'], 'strftime'):
                        row['Date_'] = row['Date_'].strftime('%Y-%m-%d')
                    else:
                        row['Date_'] = str(row['Date_'])[:10]
                for key in ['TotalQty', 'TotalVolume', 'TotalExw', 'TotalDdp']:
                    if row.get(key) is not None:
                        row[key] = round(float(row[key]), 2)
            return rows

        rows_stock = format_rows(rows_stock)
        rows_incomes = format_rows(rows_incomes)

        # 5. Считаем агрегированную статистику для обоих наборов
        def calc_stats(rows):
            total_qty = sum(float(r['TotalQty'] or 0) for r in rows)
            total_volume = sum(float(r['TotalVolume'] or 0) for r in rows)
            total_exw = sum(float(r['TotalExw'] or 0) for r in rows)
            total_ddp = sum(float(r['TotalDdp'] or 0) for r in rows)
            return {
                'total_qty': round(total_qty, 2),
                'total_volume': round(total_volume, 2),
                'total_exw': round(total_exw, 2),
                'total_ddp': round(total_ddp, 2),
            }

        stats_stock = calc_stats(rows_stock)
        stats_incomes = calc_stats(rows_incomes)

        return JsonResponse({
            'success': True,
            'data_stock': rows_stock,
            'data_incomes': rows_incomes,
            'filters': filters,
            'stats_stock': stats_stock,
            'stats_incomes': stats_incomes
        })

    except Exception as e:
        logger.error(f"Ошибка API: {e}")
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        if conn:
            conn.close()