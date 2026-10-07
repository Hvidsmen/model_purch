from . import views
from django.urls import include, path

urlpatterns = [
    path("", views.index, name="index"),
    path("load_plan_by_chanel", views.load_plan_by_chanel, name="load_plan_chanel"),
    path("load_percentage_subdivision", views.load_percentage_subdivision, name="load_percentage_subdivision"),
    path("subdivision_percent", views.load_percent_season, name="load_percent_season"),
    path("split_model", views.split_model, name="split_model"),

]
