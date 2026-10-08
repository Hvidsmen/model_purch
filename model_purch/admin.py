from django.contrib import admin
from .models import *

admin.site.register(KindPurch)
admin.site.register(PGGoods)

admin.site.register(Purch)
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
    list_display = ("scenario", "price_per_container")


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
