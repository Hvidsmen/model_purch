from django.urls import path
from . import views, views_freight
from . import  views_graph
urlpatterns = [
    path("freight/", views_freight.freight_page, name="freight"),
    path("freight/copy/", views_freight.copy_freight, name="copy_freight"),
    # ==============================================================================
    # PGGoods
    # ==============================================================================
    path('pggoods_list', views.pggoods_list, name='pggoods_list'),
    path('pggoods/<int:pk>/edit/', views.edit_pggoods, name='edit_pggoods'),
    path('pggoods/<int:pk>/delete/', views.delete_pggoods, name='delete_pggoods'),
    path('pggoods/bulk-update/', views.bulk_update_pggoods, name='bulk_update_pggoods'),
    path('pggoods/export/', views.export_to_excel, name='export_pggoods'),
    path('pggoods/import/', views.import_from_excel, name='import_from_excel'),

    # ==============================================================================
    # Сценарии
    # ==============================================================================
    path('', views.scenario_list, name='scenario_list_base'),
    path('scenarios/', views.scenario_list, name='scenario_list'),
    path('scenarios/export-sql/', views.bulk_export_scenarios, name='bulk_export_scenarios'),
    path('scenarios/create/', views.scenario_create, name='scenario_create'),
    path('scenarios/<int:pk>/edit/', views.scenario_edit, name='scenario_edit'),
    path('scenarios/<int:pk>/delete/', views.scenario_delete, name='scenario_delete'),

    # ==============================================================================
    # Purch (Закупки)
    # ==============================================================================
    path('purch/', views.purch_list, name='purch_list'),
    path('purch/create/', views.purch_create, name='purch_create'),
    path('purch/<int:pk>/edit/', views.purch_edit, name='purch_edit'),
    path('purch/<int:pk>/delete/', views.purch_delete, name='purch_delete'),

    path('purch/copy/', views.copy_purch_from_scenario, name='copy_purch_from_scenario'),
    path('pggoods/copy/', views.copy_pggoods_from_scenario, name='copy_pggoods_from_scenario'),
    path('scenarios/<int:pk>/export-sql/', views.export_scenario_to_sql, name='export_scenario_to_sql'),

    # Страница результатов
    path('results/', views.results_page, name='results_page'),

    # API для пошагового алгоритма
    path('api/algorithm/start/', views.start_algorithm_api, name='start_algorithm_api'),
    path('api/algorithm/<int:run_id>/cancel/', views.cancel_algorithm_api, name='cancel_algorithm_api'),
    path('api/algorithm/<int:run_id>/execute-next/', views.execute_next_step_api, name='execute_next_step_api'),
    path('api/algorithm/<int:run_id>/status/', views.get_algorithm_status_api, name='get_algorithm_statuэ'),

    path('stock-chart/', views_graph.stock_chart_page, name='stock_chart_page'),
    path('api/stock-chart-data/', views_graph.stock_chart_data_api, name='stock_chart_data_api')
]
