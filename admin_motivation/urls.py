from . import views
from django.urls import include, path

urlpatterns = [
    path("", views.index, name="index_admin_motivation"),
    path("global_coeff", views.gloabal_coeff, name="global_coeff_admin_motivation"),
    path("goods_matrix", views.goods_matrix, name="goods_matrix_admin_motivation"),
    path("coeff_subdivisions_admin_motivation/<int:subdivision>", views.coef_one_sub,
         name="coeff_subdivisions_admin_motivation"),
    path("gb_act", views.gloabal_coeff_action, name="gb_act"),
    path("sub_act/<int:subdivision>", views.sub_act, name="sub_act"),
    path("loader_motive", views.loader_motive, name="loader_motive"),

]
