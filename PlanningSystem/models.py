from django.db import models


class TemplatesFile(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to='excel')

class OptionPlanRef(models.Model):
    id = models.AutoField(primary_key=True)
    option_key = models.CharField(max_length=255)
    option_name = models.CharField(max_length=255)

    def __str__(self):
        return self.option_name

    @classmethod
    def get_or_create(cls, name):
        if len(cls.objects.filter(option_key=name)) == 0:
            obj = cls.objects.create(option_key=name, option_name=name)
        else:
            obj = cls.objects.get(option_key=name)
        return obj


class BrangRef(models.Model):
    id = models.AutoField(primary_key=True)
    brand_name = models.CharField(max_length=255)

    def __str__(self):
        return self.brand_name

    @classmethod
    def get_or_create(cls, brand_name):
        if len(cls.objects.filter(brand_name=brand_name)) == 0:
            obj = cls.objects.create(brand_name=brand_name)
        else:
            obj = cls.objects.get(brand_name=brand_name)

        return obj


class GroupERPRef(models.Model):
    id = models.AutoField(primary_key=True)
    group_ERP_name = models.CharField(max_length=255)

    def __str__(self):
        return self.group_ERP_name

    @classmethod
    def get_or_create(cls, name):
        if len(cls.objects.filter(group_ERP_name=name)) == 0:
            obj = cls.objects.create(group_ERP_name=name)
        else:
            obj = cls.objects.get(group_ERP_name=name)
        return obj


class GroupENRef(models.Model):
    id = models.AutoField(primary_key=True)
    group_EN_name = models.CharField(max_length=255)

    def __str__(self):
        return self.group_EN_name

    @classmethod
    def get_or_create(cls, name):
        if len(cls.objects.filter(group_EN_name=name)) == 0:
            obj = cls.objects.create(group_EN_name=name)
        else:
            obj = cls.objects.get(group_EN_name=name)
        return obj


class PlanningGroupRef(models.Model):
    id = models.AutoField(primary_key=True)
    planning_group_name = models.CharField(max_length=255)

    def __str__(self):
        return self.planning_group_name

    @classmethod
    def get_or_create(cls, name):
        if len(cls.objects.filter(planning_group_name=name)) == 0:
            obj = cls.objects.create(planning_group_name=name)
        else:
            obj = cls.objects.get(planning_group_name=name)
        return obj


class PlanningGroupSalesRef(models.Model):
    id = models.AutoField(primary_key=True)
    planning_group_sales_name = models.CharField(max_length=255)

    def __str__(self):
        return self.planning_group_sales_name

    @classmethod
    def get_or_create(cls, name):
        if len(cls.objects.filter(planning_group_sales_name=name)) == 0:
            obj = cls.objects.create(planning_group_sales_name=name)
        else:
            obj = cls.objects.get(planning_group_sales_name=name)
        return obj


class TypeGoodsRef(models.Model):
    id = models.AutoField(primary_key=True)
    type_goods_name = models.CharField(max_length=255)

    def __str__(self):
        return self.type_goods_name

    @classmethod
    def get_or_create(cls, name):
        if len(cls.objects.filter(type_goods_name=name)) == 0:
            obj = cls.objects.create(type_goods_name=name)
        else:
            obj = cls.objects.get(type_goods_name=name)
        return obj


class GoodsRef(models.Model):
    id = models.AutoField(primary_key=True)
    goods_key = models.CharField(max_length=255)
    type_goods = models.ForeignKey(TypeGoodsRef, on_delete=models.CASCADE, null=True)

    brand = models.ForeignKey(BrangRef, on_delete=models.CASCADE, null=True)
    group_ERP = models.ForeignKey(GroupERPRef, on_delete=models.CASCADE, null=True)
    group_EN = models.ForeignKey(GroupENRef, on_delete=models.CASCADE, null=True)
    planning_group = models.ForeignKey(PlanningGroupRef, on_delete=models.CASCADE, null=True)
    planning_group_sales = models.ForeignKey(PlanningGroupSalesRef, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return self.goods_key


class ChanelGlobal(models.Model):
    id = models.AutoField(primary_key=True)
    chanel_global_name = models.CharField(max_length=255)

    def __str__(self):
        return self.chanel_global_name

    @classmethod
    def get_or_create(cls, name):
        if len(cls.objects.filter(chanel_global_name=name)) == 0:
            obj = cls.objects.create(chanel_global_name=name)
        else:
            obj = cls.objects.get(chanel_global_name=name)
        return obj


class ChanelRef(models.Model):
    id = models.AutoField(primary_key=True)
    chanel_key = models.CharField(max_length=255)
    chanel_name = models.CharField(max_length=255)
    chanel_global = models.ForeignKey(ChanelGlobal, on_delete=models.CASCADE, null=True)

    is_subdivision = models.BooleanField(default=False, null=True)

    def __str__(self):
        return self.chanel_key

    @classmethod
    def get_or_create(cls, name):
        if len(cls.objects.filter(chanel_key=name)) == 0:
            obj = cls.objects.create(chanel_key=name, chanel_name=name)
        else:
            obj = cls.objects.get(chanel_key=name)
        return obj


class SubdivisionGlobalRef(models.Model):
    id = models.AutoField(primary_key=True)
    subdivision_global_key = models.CharField(max_length=255)

    def __str__(self):
        return self.subdivision_global_key

    @classmethod
    def get_or_create(cls, name):
        if len(cls.objects.filter(subdivision_global_key=name)) == 0:
            obj = cls.objects.create(subdivision_global_key=name)
        else:
            obj = cls.objects.get(subdivision_global_key=name)
        return obj


class SubdivisionRef(models.Model):
    id = models.AutoField(primary_key=True)
    subdivision_key = models.CharField(max_length=255)
    subdivision_name = models.CharField(max_length=255)
    chanel = models.ForeignKey(ChanelGlobal, on_delete=models.CASCADE, null=True)

    subdivision_global = models.ForeignKey(SubdivisionGlobalRef, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return self.subdivision_key


class PeriodPlanRef(models.Model):
    id = models.AutoField(primary_key=True)
    perion_plan_name = models.CharField(max_length=255)

    def __str__(self):
        return self.perion_plan_name

# Create your models here.
