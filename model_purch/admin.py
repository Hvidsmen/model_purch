from django.contrib import admin
from .models import *

admin.site.register(KindPurch)
@admin.register(PGGoods)
class PGGoodsAdmin(admin.ModelAdmin):
    readonly_fields = ('scenario_plan', 'planning_group', 'planning_group_key', 'percent_stock_end', 'freight_usd',
                       'cif_usd', 'customs_payment_usd', 'warehouse_delivery_usd', 'ddp_usd', 'kddp')


@admin.register(GoodsGroup)
class GoodsGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'duty_rate')
    search_fields = ('name',)


@admin.register(Purch)
class PurchAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_russian', 'lag_income')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .services.pricing import reprice_goods
        reprice_goods(strict=False)

admin.site.register(PurchPay)


admin.site.register(ResultCalc)


admin.site.register(KindLagPay)

@admin.register(PGGoodsDuplicateArchive)
class PGGoodsDuplicateArchiveAdmin(admin.ModelAdmin):
    list_display = ('original_id', 'scenario_id', 'kept_id', 'archived_at')
    readonly_fields = ('original_id', 'scenario_id', 'kept_id', 'original_data', 'archived_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Freight)
class FreightAdmin(admin.ModelAdmin):
    list_display = ("scenario", "price_per_container", "customs_rate", "warehouse_delivery_cost")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .services.pricing import reprice_goods
        reprice_goods(obj.scenario, strict=False)



@admin.register(ScenarioExport)
class ScenarioExportAdmin(admin.ModelAdmin):
    list_display = ('scenario', 'exported_at', 'fingerprint')
    readonly_fields = ('scenario', 'exported_at', 'fingerprint', 'parameters')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchScenarioArchive)
class PurchScenarioArchiveAdmin(PGGoodsDuplicateArchiveAdmin):
    list_display = ('original_id', 'kept_id', 'archived_at')
    readonly_fields = ('original_id', 'kept_id', 'original_data', 'payments', 'archived_at')
