from django.contrib import admin
from django.contrib import admin
from .models import *

admin.site.register(Goods)
@admin.register(GlobalCoeff)
class GlobalCoeffAdmin(admin.ModelAdmin):
    list_display = ('version', 'goods', 'segment', 'type_coeff', 'motivation_coeff')
    list_filter = ('version',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GlobalCoeffVersion)
class GlobalCoeffVersionAdmin(GlobalCoeffAdmin):
    list_display = ('effective_from', 'title', 'created_at')
    list_filter = ()
admin.site.register(TypeCoeff)
admin.site.register(SegmentCoeff)
admin.site.register(VariationCalculate)


admin.site.register(Brand)
admin.site.register(PlanningGroupSales)
admin.site.register(GroupGoods)
admin.site.register(Subdivision)
admin.site.register(Chanel)

admin.site.register(KindManagerCoeff)
admin.site.register(SubdivisionManagerCoeff)
admin.site.register(ExampleFiles)
# Register your models here.
