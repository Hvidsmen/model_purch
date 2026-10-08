from django.contrib import admin
from .models import *

# Classification references are shared by all historical snapshots.
class CoefficientReferenceAdmin(admin.ModelAdmin):
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

admin.site.register(Goods, CoefficientReferenceAdmin)
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
    list_display = ('effective_from', 'title', 'status', 'approved_at', 'replaced_by')
    list_filter = ()
admin.site.register(TypeCoeff, CoefficientReferenceAdmin)
admin.site.register(SegmentCoeff, CoefficientReferenceAdmin)
admin.site.register(VariationCalculate, CoefficientReferenceAdmin)


admin.site.register(Brand)
admin.site.register(PlanningGroupSales)
admin.site.register(GroupGoods)
admin.site.register(Subdivision, CoefficientReferenceAdmin)
admin.site.register(Chanel)

admin.site.register(KindManagerCoeff, CoefficientReferenceAdmin)
@admin.register(SubdivisionCoeff)
class SubdivisionCoeffAdmin(GlobalCoeffAdmin):
    list_display = ('version', 'subdivision', 'goods', 'segment', 'type_coeff', 'motivation_coeff')
    list_filter = ('version', 'subdivision')


@admin.register(SubdivisionManagerCoeff)
class SubdivisionManagerCoeffAdmin(GlobalCoeffAdmin):
    list_display = ('version', 'subdivision', 'kind', 'coeff')
    list_filter = ('version', 'subdivision')
admin.site.register(ExampleFiles)
# Register your models here.


@admin.register(MotivationApproval)
class MotivationApprovalAdmin(GlobalCoeffAdmin):
    list_display = ('version', 'created_at', 'plan', 'baseline')
    list_filter = ()
