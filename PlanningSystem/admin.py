from django.contrib import admin
from .models import *

admin.site.register(TemplatesFile)
admin.site.register(OptionPlanRef)
admin.site.register(BrangRef)
admin.site.register(GroupERPRef)
admin.site.register(GroupENRef)
admin.site.register(PlanningGroupRef)
admin.site.register(PlanningGroupSalesRef)
admin.site.register(GoodsRef)
admin.site.register(ChanelRef)
admin.site.register(SubdivisionRef)
admin.site.register(ChanelGlobal)
admin.site.register(TypeGoodsRef)

# Register your models here.
from .models_algorithm import *

admin.site.register(PlanSalesByChanelHeader)
admin.site.register(PeriodPlanRef)
admin.site.register(PlanSalesByChanelGoods)

admin.site.register(PercentSubdivisionHeader)
admin.site.register(PecentSubdivisionGoods)


admin.site.register(PercentSubdivisionSeason)
