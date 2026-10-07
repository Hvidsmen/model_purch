"""
Dash-приложение для графика остатков.
Интегрировано в Django через django-plotly-dash.
"""

import pandas as pd
import pyodbc
import plotly.express as px
import plotly.graph_objects as go
from django.conf import settings
from django_plotly_dash import DjangoDash
from dash import dcc, html, Input, Output, callback
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# Строка подключения к MS SQL
MS_SQL_CONN_STR = getattr(settings, 'MS_SQL_CONN_STR', None)


def get_db_connection():
    """Создает подключение к БД"""
    return pyodbc.connect(MS_SQL_CONN_STR)


def load_data(group_goods=None, planning_sales=None, planning_group=None):
    """Загружает данные из vStockOnDatePlan с учетом фильтров"""
    if not MS_SQL_CONN_STR:
        logger.error("MS_SQL_CONN_STR не настроен в settings.py")
        return pd.DataFrame()

    conn = None
    try:
        conn = get_db_connection()

        base_sql = """
            SELECT 
                [Date_],
                [PlanningGroupOZP],
                [group_goods],
                [planning_sales],
                [Qty],
                [exw_usd],
                [ddp_usd],
                [volume]
            FROM [ModelPurch].[dbo].[vStockOnDatePlan]
            WHERE 1=1
        """
        params = []

        if group_goods:
            base_sql += " AND group_goods = ?"
            params.append(group_goods)
        if planning_sales:
            base_sql += " AND planning_sales = ?"
            params.append(planning_sales)
        if planning_group:
            base_sql += " AND PlanningGroupOZP = ?"
            params.append(planning_group)

        base_sql += " ORDER BY [Date_] ASC"

        df = pd.read_sql(base_sql, conn, params=params)
        df['Date_'] = pd.to_datetime(df['Date_'])

        # Агрегируем по дате
        df['TotalExw'] = df['Qty'] * df['exw_usd']
        df['TotalDdp'] = df['Qty'] * df['ddp_usd']

        agg_df = df.groupby('Date_').agg(
            TotalQty=('Qty', 'sum'),
            TotalVolume=('volume', 'sum'),
            TotalExw=('TotalExw', 'sum'),
            TotalDdp=('TotalDdp', 'sum')
        ).reset_index()

        return agg_df

    except Exception as e:
        logger.error(f"Ошибка загрузки данных: {e}")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()


def get_filter_options():
    """Получает уникальные значения для фильтров"""
    if not MS_SQL_CONN_STR:
        return {'group_goods': [], 'planning_sales': [], 'planning_group': []}

    conn = None
    try:
        conn = get_db_connection()

        group_goods = pd.read_sql(
            "SELECT DISTINCT group_goods FROM [ModelPurch].[dbo].[vStockOnDatePlan] "
            "WHERE group_goods IS NOT NULL AND group_goods != '' ORDER BY group_goods",
            conn
        )['group_goods'].tolist()

        planning_sales = pd.read_sql(
            "SELECT DISTINCT planning_sales FROM [ModelPurch].[dbo].[vStockOnDatePlan] "
            "WHERE planning_sales IS NOT NULL AND planning_sales != '' ORDER BY planning_sales",
            conn
        )['planning_sales'].tolist()

        planning_group = pd.read_sql(
            "SELECT DISTINCT PlanningGroupOZP FROM [ModelPurch].[dbo].[vStockOnDatePlan] "
            "WHERE PlanningGroupOZP IS NOT NULL AND PlanningGroupOZP != '' ORDER BY PlanningGroupOZP",
            conn
        )['PlanningGroupOZP'].tolist()

        return {
            'group_goods': group_goods,
            'planning_sales': planning_sales,
            'planning_group': planning_group,
        }
    except Exception as e:
        logger.error(f"Ошибка получения фильтров: {e}")
        return {'group_goods': [], 'planning_sales': [], 'planning_group': []}
    finally:
        if conn:
            conn.close()


# === СОЗДАНИЕ DASH-ПРИЛОЖЕНИЯ ===
# Используем DjangoDash вместо обычного dash.Dash

app = DjangoDash(
    'StockChartApp',
    suppress_callback_exceptions=True,
)



# Загружаем опции фильтров при старте
FILTER_OPTIONS = get_filter_options()
# === LAYOUT (ИНТЕРФЕЙС) ===

app.layout = html.Div([
    # Блок фильтров
    html.Div([
        html.H5([
            html.I(className="bi bi-funnel me-2"),
            "Фильтры"
        ], style={'marginBottom': '16px', 'color': '#495057'}),

        html.Div([
            # Фильтр: Группа товаров
            html.Div([
                html.Label("Группа товаров", style={'fontWeight': 600, 'marginBottom': '6px', 'display': 'block'}),
                dcc.Dropdown(
                    id='filter-group-goods',
                    options=[{'label': '— Все —', 'value': ''}] +
                            [{'label': g, 'value': g} for g in FILTER_OPTIONS['group_goods']],
                    value='',
                    clearable=False,
                    style={'width': '100%'}
                ),
            ], style={'flex': 1, 'minWidth': '220px'}),

            # Фильтр: План продаж
            html.Div([
                html.Label("План продаж", style={'fontWeight': 600, 'marginBottom': '6px', 'display': 'block'}),
                dcc.Dropdown(
                    id='filter-planning-sales',
                    options=[{'label': '— Все —', 'value': ''}] +
                            [{'label': p, 'value': p} for p in FILTER_OPTIONS['planning_sales']],
                    value='',
                    clearable=False,
                    style={'width': '100%'}
                ),
            ], style={'flex': 1, 'minWidth': '220px'}),

            # Фильтр: Группа планирования OZP
            html.Div([
                html.Label("Группа планирования OZP",
                           style={'fontWeight': 600, 'marginBottom': '6px', 'display': 'block'}),
                dcc.Dropdown(
                    id='filter-planning-group',
                    options=[{'label': '— Все —', 'value': ''}] +
                            [{'label': p, 'value': p} for p in FILTER_OPTIONS['planning_group']],
                    value='',
                    clearable=False,
                    style={'width': '100%'}
                ),
            ], style={'flex': 1, 'minWidth': '220px'}),

            # Переключатель типа графика
            html.Div([
                html.Label("Тип графика", style={'fontWeight': 600, 'marginBottom': '6px', 'display': 'block'}),
                dcc.RadioItems(
                    id='chart-type',
                    options=[
                        {'label': ' 📈 Линия', 'value': 'line'},
                        {'label': ' 📊 Столбцы', 'value': 'bar'},
                    ],
                    value='line',
                    inline=True,
                    style={'marginTop': '8px'}
                ),
            ], style={'minWidth': '220px'}),

        ], style={
            'display': 'flex',
            'gap': '16px',
            'flexWrap': 'wrap',
        }),

    ], style={
        'background': 'white',
        'padding': '20px',
        'borderRadius': '12px',
        'boxShadow': '0 2px 8px rgba(0,0,0,0.05)',
        'marginBottom': '20px',
    }),

    # Блок статистики
    html.Div([
        html.Div([
            html.Div("Всего Qty", style={'fontSize': '0.85rem', 'color': '#6c757d', 'textTransform': 'uppercase'}),
            html.Div(id='stat-total-qty', children="0",
                     style={'fontSize': '1.5rem', 'fontWeight': 700, 'color': '#0d6efd'}),
        ], style={'flex': 1, 'textAlign': 'center', 'padding': '16px', 'background': '#f8f9fa',
                  'borderRadius': '12px'}),

        html.Div([
            html.Div("Всего Volume", style={'fontSize': '0.85rem', 'color': '#6c757d', 'textTransform': 'uppercase'}),
            html.Div(id='stat-total-volume', children="0",
                     style={'fontSize': '1.5rem', 'fontWeight': 700, 'color': '#198754'}),
        ], style={'flex': 1, 'textAlign': 'center', 'padding': '16px', 'background': '#f8f9fa',
                  'borderRadius': '12px'}),

        html.Div([
            html.Div("EXW USD", style={'fontSize': '0.85rem', 'color': '#6c757d', 'textTransform': 'uppercase'}),
            html.Div(id='stat-total-exw', children="$0",
                     style={'fontSize': '1.5rem', 'fontWeight': 700, 'color': '#fd7e14'}),
        ], style={'flex': 1, 'textAlign': 'center', 'padding': '16px', 'background': '#f8f9fa',
                  'borderRadius': '12px'}),

        html.Div([
            html.Div("DDP USD", style={'fontSize': '0.85rem', 'color': '#6c757d', 'textTransform': 'uppercase'}),
            html.Div(id='stat-total-ddp', children="$0",
                     style={'fontSize': '1.5rem', 'fontWeight': 700, 'color': '#dc3545'}),
        ], style={'flex': 1, 'textAlign': 'center', 'padding': '16px', 'background': '#f8f9fa',
                  'borderRadius': '12px'}),

    ], style={
        'display': 'flex',
        'gap': '16px',
        'marginBottom': '20px',
        'flexWrap': 'wrap',
    }),

    # График
    html.Div([
        dcc.Loading(
            id="loading-chart",
            type="circle",
            color="#0d6efd",
            children=dcc.Graph(
                id='stock-chart',
                figure=go.Figure(),
                config={
                    'displayModeBar': True,
                    'displaylogo': False,
                    'locale': 'ru',
                },
                style={'height': '500px'}
            )
        ),
    ], style={
        'background': 'white',
        'padding': '24px',
        'borderRadius': '12px',
        'boxShadow': '0 2px 8px rgba(0,0,0,0.05)',
    }),
])


# === CALLBACK'И (ИНТЕРАКТИВНОСТЬ) ===

@app.callback(
    [Output('stock-chart', 'figure'),
     Output('stat-total-qty', 'children'),
     Output('stat-total-volume', 'children'),
     Output('stat-total-exw', 'children'),
     Output('stat-total-ddp', 'children')],
    [Input('filter-group-goods', 'value'),
     Input('filter-planning-sales', 'value'),
     Input('filter-planning-group', 'value'),
     Input('chart-type', 'value')]
)
def update_dashboard(group_goods, planning_sales, planning_group, chart_type):
    """Обновляет график и статистику при изменении фильтров"""

    # Загружаем данные
    df = load_data(
        group_goods=group_goods if group_goods else None,
        planning_sales=planning_sales if planning_sales else None,
        planning_group=planning_group if planning_group else None,
    )

    # === Формируем график ===
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Нет данных для отображения",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(size=20, color="#6c757d")
        )
        fig.update_layout(
            template='plotly_white',
            height=500,
            margin=dict(l=40, r=20, t=40, b=40)
        )
        return fig, "0", "0", "$0", "$0"

    # Создаем график в зависимости от типа
    if chart_type == 'line':
        fig = px.line(
            df, x='Date_', y='TotalQty',
            labels={'Date_': 'Дата', 'TotalQty': 'Qty'},
        )
        fig.update_traces(
            line=dict(width=3, color='#0d6efd'),
            fill='tozeroy',
            fillcolor='rgba(13, 110, 253, 0.1)',
            marker=dict(size=6)
        )
    else:
        fig = px.bar(
            df, x='Date_', y='TotalQty',
            labels={'Date_': 'Дата', 'TotalQty': 'Qty'},
        )
        fig.update_traces(marker_color='#0d6efd')

    # Настройка макета
    fig.update_layout(
        template='plotly_white',
        height=500,
        margin=dict(l=40, r=20, t=40, b=40),
        hovermode='x unified',
        xaxis=dict(
            title='Дата',
            gridcolor='rgba(0,0,0,0.05)',
            tickformat='%d.%m.%Y',
        ),
        yaxis=dict(
            title='Qty',
            gridcolor='rgba(0,0,0,0.05)',
            tickformat=',',
        ),
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family='Arial, sans-serif'),
    )

    # Добавляем среднее значение как линию
    avg_qty = df['TotalQty'].mean()
    fig.add_hline(
        y=avg_qty,
        line_dash="dash",
        line_color="#6c757d",
        annotation_text=f"Среднее: {avg_qty:,.0f}",
        annotation_position="top right"
    )

    # === Считаем статистику ===
    total_qty = df['TotalQty'].sum()
    total_volume = df['TotalVolume'].sum()
    total_exw = df['TotalExw'].sum()
    total_ddp = df['TotalDdp'].sum()

    def format_number(value, prefix='', decimals=0):
        return f"{prefix}{value:,.{decimals}f}".replace(',', ' ')

    stat_qty = format_number(total_qty)
    stat_volume = format_number(total_volume, decimals=2)
    stat_exw = format_number(total_exw, prefix='$', decimals=2)
    stat_ddp = format_number(total_ddp, prefix='$', decimals=2)

    return fig, stat_qty, stat_volume, stat_exw, stat_ddp