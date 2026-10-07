"""
Отдельное Dash-приложение для графика остатков.
Запуск: python model_purch/dash_app.py
"""

import dash
from dash import dcc, html, Input, Output
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from sqlalchemy import create_engine
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Строка подключения через SQLAlchemy (рекомендуемый способ)
# Формат: mssql+pyodbc://server/database?driver=SQL+Server&Trusted_Connection=yes
DATABASE_URI = "mssql+pyodbc://vm-dwh/ModelPurch?driver=SQL+Server&Trusted_Connection=yes"

# Создаем движок SQLAlchemy один раз при старте
engine = create_engine(DATABASE_URI)


def load_data(group_goods=None, planning_sales=None, planning_group=None):
    """Загрузка данных из vStockOnDatePlan"""
    try:
        sql = """
            SELECT [Date_], [PlanningGroupOZP], [group_goods], [planning_sales],
                   [Qty], [exw_usd], [ddp_usd], [volume]
            FROM [ModelPurch].[dbo].[vStockOnDatePlan]
            WHERE 1=1
        """
        params = []
        if group_goods:
            sql += " AND group_goods = ?"; params.append(group_goods)
        if planning_sales:
            sql += " AND planning_sales = ?"; params.append(planning_sales)
        if planning_group:
            sql += " AND PlanningGroupOZP = ?"; params.append(planning_group)
        sql += " ORDER BY [Date_] ASC"

        # Теперь pandas использует SQLAlchemy, предупреждений не будет
        df = pd.read_sql(sql, engine, params=params)
        df['Date_'] = pd.to_datetime(df['Date_'])
        df['TotalExw'] = df['Qty'] * df['exw_usd']
        df['TotalDdp'] = df['Qty'] * df['ddp_usd']

        return df.groupby('Date_').agg(
            TotalQty=('Qty', 'sum'),
            TotalVolume=('volume', 'sum'),
            TotalExw=('TotalExw', 'sum'),
            TotalDdp=('TotalDdp', 'sum')
        ).reset_index()
    except Exception as e:
        logger.error(f"Ошибка загрузки данных: {e}")
        return pd.DataFrame()


def get_filter_options():
    """Получение уникальных значений для фильтров"""
    try:
        return {
            'group_goods': pd.read_sql(
                "SELECT DISTINCT group_goods FROM [ModelPurch].[dbo].[vStockOnDatePlan] "
                "WHERE group_goods IS NOT NULL AND group_goods != '' ORDER BY group_goods", engine
            )['group_goods'].tolist(),
            'planning_sales': pd.read_sql(
                "SELECT DISTINCT planning_sales FROM [ModelPurch].[dbo].[vStockOnDatePlan] "
                "WHERE planning_sales IS NOT NULL AND planning_sales != '' ORDER BY planning_sales", engine
            )['planning_sales'].tolist(),
            'planning_group': pd.read_sql(
                "SELECT DISTINCT PlanningGroupOZP FROM [ModelPurch].[dbo].[vStockOnDatePlan] "
                "WHERE PlanningGroupOZP IS NOT NULL AND PlanningGroupOZP != '' ORDER BY PlanningGroupOZP", engine
            )['PlanningGroupOZP'].tolist(),
        }
    except Exception as e:
        logger.error(f"Ошибка получения фильтров: {e}")
        return {'group_goods': [], 'planning_sales': [], 'planning_group': []}


# === СОЗДАНИЕ DASH-ПРИЛОЖЕНИЯ ===
app = dash.Dash(__name__, title="График остатков", suppress_callback_exceptions=True)
app.server.secret_key = 'django-insecure-4qn-1xzi$z)hg+^#icbrwsl!=fi3b*mtn9!74vil*33g6kc8jb'

FILTER_OPTIONS = get_filter_options()

app.layout = html.Div([
    html.Div([
        html.H2("📊 График остатков", style={'margin': 0, 'color': '#212529'}),
        html.P("Интерактивная аналитика по данным vStockOnDatePlan", style={'color': '#6c757d', 'margin': '4px 0 0 0'}),
    ], style={'background': 'white', 'padding': '24px', 'borderRadius': '12px', 'boxShadow': '0 2px 8px rgba(0,0,0,0.08)', 'borderLeft': '4px solid #0d6efd', 'marginBottom': '20px'}),

    html.Div([
        html.Div([
            html.Label("Группа товаров", style={'fontWeight': 600}),
            dcc.Dropdown(id='filter-group-goods', options=[{'label': '— Все —', 'value': ''}] + [{'label': g, 'value': g} for g in FILTER_OPTIONS['group_goods']], value='', clearable=False),
        ], style={'flex': 1, 'minWidth': '220px'}),
        html.Div([
            html.Label("План продаж", style={'fontWeight': 600}),
            dcc.Dropdown(id='filter-planning-sales', options=[{'label': '— Все —', 'value': ''}] + [{'label': p, 'value': p} for p in FILTER_OPTIONS['planning_sales']], value='', clearable=False),
        ], style={'flex': 1, 'minWidth': '220px'}),
        html.Div([
            html.Label("Группа планирования OZP", style={'fontWeight': 600}),
            dcc.Dropdown(id='filter-planning-group', options=[{'label': '— Все —', 'value': ''}] + [{'label': p, 'value': p} for p in FILTER_OPTIONS['planning_group']], value='', clearable=False),
        ], style={'flex': 1, 'minWidth': '220px'}),
        html.Div([
            html.Label("Тип графика", style={'fontWeight': 600}),
            dcc.RadioItems(id='chart-type', options=[{'label': ' 📈 Линия', 'value': 'line'}, {'label': ' 📊 Столбцы', 'value': 'bar'}], value='line', inline=True),
        ], style={'minWidth': '220px'}),
    ], style={'display': 'flex', 'gap': '16px', 'flexWrap': 'wrap', 'background': 'white', 'padding': '20px', 'borderRadius': '12px', 'boxShadow': '0 2px 8px rgba(0,0,0,0.05)', 'marginBottom': '20px'}),

    html.Div([
        html.Div([html.Div("Всего Qty", style={'fontSize': '0.85rem', 'color': '#6c757d'}), html.Div(id='stat-qty', children="0", style={'fontSize': '1.5rem', 'fontWeight': 700, 'color': '#0d6efd'})], style={'flex': 1, 'textAlign': 'center', 'padding': '16px', 'background': '#f8f9fa', 'borderRadius': '12px'}),
        html.Div([html.Div("Volume", style={'fontSize': '0.85rem', 'color': '#6c757d'}), html.Div(id='stat-volume', children="0", style={'fontSize': '1.5rem', 'fontWeight': 700, 'color': '#198754'})], style={'flex': 1, 'textAlign': 'center', 'padding': '16px', 'background': '#f8f9fa', 'borderRadius': '12px'}),
        html.Div([html.Div("EXW USD", style={'fontSize': '0.85rem', 'color': '#6c757d'}), html.Div(id='stat-exw', children="$0", style={'fontSize': '1.5rem', 'fontWeight': 700, 'color': '#fd7e14'})], style={'flex': 1, 'textAlign': 'center', 'padding': '16px', 'background': '#f8f9fa', 'borderRadius': '12px'}),
        html.Div([html.Div("DDP USD", style={'fontSize': '0.85rem', 'color': '#6c757d'}), html.Div(id='stat-ddp', children="$0", style={'fontSize': '1.5rem', 'fontWeight': 700, 'color': '#dc3545'})], style={'flex': 1, 'textAlign': 'center', 'padding': '16px', 'background': '#f8f9fa', 'borderRadius': '12px'}),
    ], style={'display': 'flex', 'gap': '16px', 'marginBottom': '20px', 'flexWrap': 'wrap'}),

    html.Div([
        dcc.Loading(id="loading-chart", type="circle", color="#0d6efd", children=dcc.Graph(id='stock-chart', figure=go.Figure(), style={'height': '500px'})),
    ], style={'background': 'white', 'padding': '24px', 'borderRadius': '12px', 'boxShadow': '0 2px 8px rgba(0,0,0,0.05)'}),
], style={'fontFamily': 'Arial, sans-serif', 'padding': '20px', 'background': '#f4f4f9'})


@app.callback(
    [Output('stock-chart', 'figure'), Output('stat-qty', 'children'), Output('stat-volume', 'children'), Output('stat-exw', 'children'), Output('stat-ddp', 'children')],
    [Input('filter-group-goods', 'value'), Input('filter-planning-sales', 'value'), Input('filter-planning-group', 'value'), Input('chart-type', 'value')]
)
def update_dashboard(group_goods, planning_sales, planning_group, chart_type):
    df = load_data(group_goods=group_goods or None, planning_sales=planning_sales or None, planning_group=planning_group or None)

    if df.empty:
        fig = go.Figure().add_annotation(text="Нет данных", xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False, font=dict(size=20))
        fig.update_layout(template='plotly_white', height=500)
        return fig, "0", "0", "$0", "$0"

    if chart_type == 'line':
        fig = px.line(df, x='Date_', y='TotalQty', labels={'Date_': 'Дата', 'TotalQty': 'Qty'})
        fig.update_traces(line=dict(width=3, color='#0d6efd'), fill='tozeroy', fillcolor='rgba(13, 110, 253, 0.1)', marker=dict(size=6))
    else:
        fig = px.bar(df, x='Date_', y='TotalQty', labels={'Date_': 'Дата', 'TotalQty': 'Qty'})
        fig.update_traces(marker_color='#0d6efd')

    fig.update_layout(template='plotly_white', height=500, hovermode='x unified', xaxis=dict(title='Дата', tickformat='%d.%m.%Y'), yaxis=dict(title='Qty', tickformat=','))
    fig.add_hline(y=df['TotalQty'].mean(), line_dash="dash", line_color="#6c757d", annotation_text=f"Среднее: {df['TotalQty'].mean():,.0f}", annotation_position="top right")

    fmt = lambda v, prefix='', decimals=0: f"{prefix}{v:,.{decimals}f}".replace(',', ' ')
    return (fig, fmt(df['TotalQty'].sum()), fmt(df['TotalVolume'].sum(), decimals=2), fmt(df['TotalExw'].sum(), prefix='$', decimals=2), fmt(df['TotalDdp'].sum(), prefix='$', decimals=2))


if __name__ == '__main__':
    logger.info("🚀 Запуск Dash-приложения на http://0.0.0.0:8050")
    app.run(host='0.0.0.0', port=8050, debug=True)