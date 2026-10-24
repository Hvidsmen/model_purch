from django.urls import path
from . import views

urlpatterns = [
    path('refresh/', views.refresh_data, name='dashboard_refresh'),
    path('refresh/status/', views.refresh_status, name='dashboard_refresh_status'),
    path('', views.index, name='dashboards_index'),
    path('dealers/', views.dealer_dashboard, name='dealer_dashboard'),
    path('dealers/data/', views.dealer_data, name='dealer_dashboard_data'),
    path('dealers/export/', views.dealer_export, name='dealer_dashboard_export'),
]
