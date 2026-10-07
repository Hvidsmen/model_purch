from django.contrib import admin
from django.contrib import admin
from .models import *

admin.site.register(Goods)
admin.site.register(GlobalCoeff)
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
