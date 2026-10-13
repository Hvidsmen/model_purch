from . import views, plan_views, comparison_views, director_views, report_views
from django.urls import include, path

urlpatterns = [
    path("copy_group/", director_views.coefficient_group_copy, name="motivation_copy_group"),
    path("directors/", director_views.director_directory, name="motivation_director_directory"),
    path("reports/", report_views.report_index, name="motivation_reports"),
    path("reports/<str:kind>/", report_views.report_page, name="motivation_report"),
    path("director_coefficients/global", director_views.director_coefficients, name="motivation_directors_global"),
    path("director_coefficients/local", director_views.director_coefficients, {"local": True}, name="motivation_directors_local"),
    path("coefficient_comparison", comparison_views.comparison_action, name="motivation_coefficient_comparison"),
    path("sales_plans", plan_views.sales_plans, name="motivation_sales_plans"),
    path("sales_plans/<int:scenario_id>", plan_views.sales_plans, name="motivation_sales_plan"),
    path("", views.index, name="index_admin_motivation"),
    path("global_coeff", views.gloabal_coeff, name="global_coeff_admin_motivation"),
    path("goods_matrix", views.goods_matrix, name="goods_matrix_admin_motivation"),
    path("coeff_subdivisions_admin_motivation/<int:subdivision>", views.coef_one_sub,
         name="coeff_subdivisions_admin_motivation"),
    path("gb_act", views.gloabal_coeff_action, name="gb_act"),
    path("sub_act/<int:subdivision>", views.sub_act, name="sub_act"),
    path("loader_motive", views.loader_motive, name="loader_motive"),

]
