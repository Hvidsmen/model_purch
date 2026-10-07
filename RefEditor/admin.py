from django.contrib import admin

# Register your models here.

from .models import *

admin.site.register(Subdivision)
admin.site.register(GroupOZP)
admin.site.register(SubGroupOZP)
admin.site.register(StoreGroupOZP)
admin.site.register(Reference)
