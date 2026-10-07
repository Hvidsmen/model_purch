from . import views
from django.urls import include, path

urlpatterns = [
    path('ref-store-group/', views.ref_store_group, name='re_ref_store_group'),
    path('ref-store-group/export/', views.export_stores_to_excel, name='export_stores_excel'),
]
