from django.db import models

from .connectors import connect_database


# Create your models here.


class Reference(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    table_dwh = models.CharField(max_length=255)

    example_file = models.FileField(null=True, upload_to='excel')

    def __str__(self):
        return self.name


class Subdivision(models.Model):
    id = models.AutoField(primary_key=True)
    subdivision_name = models.CharField(max_length=255)

    def __str__(self):
        return self.subdivision_name

    @classmethod
    def get_or_create(cls, sub_name):
        if len(Subdivision.objects.filter(subdivision_name=sub_name)) == 0:
            obj = Subdivision.objects.create(subdivision_name=sub_name)
            obj.save()
        return Subdivision.objects.get(subdivision_name=sub_name)


class GroupOZP(models.Model):
    id = models.AutoField(primary_key=True)
    group_ozp_name = models.CharField(max_length=255)

    def __str__(self):
        return self.group_ozp_name

    @classmethod
    def get_or_create(cls, g_name):
        if len(GroupOZP.objects.filter(group_ozp_name=g_name)) == 0:
            obj = GroupOZP.objects.create(group_ozp_name=g_name)
            obj.save()
        return GroupOZP.objects.get(group_ozp_name=g_name)


class SubGroupOZP(models.Model):
    id = models.AutoField(primary_key=True)
    sub_group_ozp_name = models.CharField(max_length=255)

    def __str__(self):
        return self.sub_group_ozp_name

    @classmethod
    def get_or_create(cls, sg_name):
        if len(SubGroupOZP.objects.filter(sub_group_ozp_name=sg_name)) == 0:
            obj = SubGroupOZP.objects.create(sub_group_ozp_name=sg_name)
            obj.save()
        return SubGroupOZP.objects.get(sub_group_ozp_name=sg_name)


class StoreGroupOZP(models.Model):
    id = models.AutoField(primary_key=True)
    key_store_sub = models.CharField(max_length=255, unique=True)

    store_name = models.CharField(max_length=255)
    subdivision = models.ForeignKey(Subdivision, on_delete=models.CASCADE)
    group_ozp = models.ForeignKey(GroupOZP, on_delete=models.CASCADE)
    sub_group_ozp = models.ForeignKey(SubGroupOZP, on_delete=models.CASCADE)

    def __str__(self):
        return self.store_name + ' ' + self.subdivision.subdivision_name

    @classmethod
    def make_key(cls, store_name, subdivision_name):
        return store_name.lower() + subdivision_name.lower()

    @classmethod
    def transfer_dwh(cls):
        stores = cls.objects.all()
        con, cur = connect_database('vm-dwh', 'DataWH')
        sql_delele = f"""
                   TRUNCATE TABLE  [DataWH].[erp].[StoreGroup]
                   
                   """
        con.execute(sql_delele)
        cur.commit()
        for store in stores:
            sql_insert = f"""INSERT INTO [DataWH].[erp].[StoreGroup]([Subdivision],[StoreName],[Group],[SupGroup])
            VALUES ('{store.subdivision.subdivision_name}','{store.store_name}','{store.group_ozp.group_ozp_name}','{store.sub_group_ozp.sub_group_ozp_name}')
            """
            try:
                con.execute(sql_insert)
                cur.commit()
            except:
                continue
