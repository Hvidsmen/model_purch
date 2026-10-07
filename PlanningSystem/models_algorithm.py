from django.db import models
import pandas as pd
import pandas as pd
import pyodbc
from openpyxl.styles.builtins import percent

from .connector_dwh import *
from .models import *
from tqdm import tqdm


class PlanSalesByChanelHeader(models.Model):
    id = models.AutoField(primary_key=True)
    opition_plan = models.ForeignKey(OptionPlanRef, on_delete=models.CASCADE)
    file = models.FileField(null=True, upload_to='excel')
    sheet_name = models.CharField(max_length=255, default='', null=True)
    period = models.ForeignKey(PeriodPlanRef, on_delete=models.CASCADE, null=True)
    year = models.IntegerField(default=2026, null=True)


from datetime import datetime, date


class PlanSalesByChanelGoods(models.Model):
    id = models.AutoField(primary_key=True)
    header = models.ForeignKey(PlanSalesByChanelHeader, on_delete=models.CASCADE)
    chanel = models.ForeignKey(ChanelRef, on_delete=models.CASCADE)
    goods = models.ForeignKey(GoodsRef, on_delete=models.CASCADE)
    qty = models.FloatField()
    amount_usd = models.FloatField()
    amount_ddp_usd = models.FloatField()

    date_plan = models.DateField()
    period = models.ForeignKey(PeriodPlanRef, on_delete=models.CASCADE)

    @classmethod
    def create_by_file(cls, header):
        file = header.file
        sheet_name = header.sheet_name
        conn, cursor = connect_database('vm-dwh', 'DataWH')

        df_in = pd.read_excel(file, sheet_name=sheet_name, skiprows=range(1, 16))

        result = {
            'Subdivision': []
            , 'GoodsKey': []
            , 'MonthInt': []
            , 'Qty': []
            , 'USD': []
            , 'DDP': []
        }
        print(df_in.head())
        for i, row in df_in.iterrows():
            sub = row[0]
            goods_key = row[1]
            month_int = 0
            for j in range(2, len(row) - 4, 4):
                month_int += 1
                result['Subdivision'].append(sub)
                result['GoodsKey'].append(goods_key)
                result['MonthInt'].append(month_int)
                result['Qty'].append(row[j])
                result['USD'].append(row[j + 1])
                result['DDP'].append(row[j + 2])

        df_res = pd.DataFrame(result)
        print(df_res.head())

        df_res = df_res.groupby(by=['Subdivision', 'GoodsKey', 'MonthInt'], as_index=False).sum()
        sql_truncate = """
         TRUNCATE TABLE [DataWH].[planning].[PlanYearByChanel]
        """
        conn.execute(sql_truncate)
        cursor.commit()
        print(df_res.shape)

        for i, row in tqdm(df_res.iterrows()):
            chanel, goods, month_int, qty, usd, ddp = row
            month_int = int(month_int)
            sql_insert = f"""
            INSERT INTO [DataWH].[planning].[PlanYearByChanel]
            (
                [Chanel]
              ,[GoodsKey]
              ,[MonthInt]
              ,[Qty]
              ,[USD]
              ,[DDP]
            )
            VALUES('{chanel}','{goods}',{int(month_int)},{qty},{usd},{ddp})
            """

            conn.execute(sql_insert)
            cursor.commit()
            if len(ChanelRef.objects.filter(chanel_key=chanel)) > 0:
                chanel_obj = ChanelRef.objects.get(chanel_key=chanel)
            else:
                chanel_obj = ChanelRef.objects.create(chanel_key=chanel, chanel_name=chanel)
                chanel_obj.save()

            goods_obj = GoodsRef.objects.get(goods_key=goods)
            date_ = date(int(header.year), month_int, 1)

            plan_goods_obj = PlanSalesByChanelGoods.objects.create(
                header=header
                , chanel=chanel_obj
                , goods=goods_obj
                , qty=qty
                , amount_usd=usd
                , amount_ddp_usd=ddp

                , date_plan=date_
                , period=header.period
            )
            plan_goods_obj.save()


class PercentSubdivisionHeader(models.Model):
    id = models.AutoField(primary_key=True)
    opition_plan = models.ForeignKey(OptionPlanRef, on_delete=models.CASCADE)
    file = models.FileField(null=True, upload_to='excel')
    sheet_name = models.CharField(max_length=255, default='', null=True)
    period = models.ForeignKey(PeriodPlanRef, on_delete=models.CASCADE, null=True)
    year = models.IntegerField(default=2026, null=True)


class PecentSubdivisionGoods(models.Model):
    id = models.AutoField(primary_key=True)
    header = models.ForeignKey(PercentSubdivisionHeader, on_delete=models.CASCADE)
    chanel = models.ForeignKey(ChanelRef, on_delete=models.CASCADE)
    subdivision = models.ForeignKey(SubdivisionRef, on_delete=models.CASCADE)
    goods = models.ForeignKey(GoodsRef, on_delete=models.CASCADE)
    percent = models.FloatField()

    @classmethod
    def create_by_file(cls, header):
        sheet_name = header.sheet_name
        file = header.file
        conn, cursor = connect_database('vm-dwh', 'DataWH')

        df_in = pd.read_excel(file, sheet_name=sheet_name)

        result = {
            'Chanel': []
            , 'Subdivision': []
            , 'GoodsKey': []
            , 'Percent_': []

        }

        print(df_in.shape)

        sql_truncate = """
        TRUNCATE TABLE DataWH.planning.PercentSubdivision
        """
        conn.execute(sql_truncate)
        cursor.commit()
        subdivision_list = df_in.columns[2:]
        for i, row in tqdm(df_in.iterrows()):
            chanel, goods = row[0], row[1]
            for j, sub in enumerate(subdivision_list):
                idx_value = j + 2
                value = row[idx_value]
                result['Chanel'].append(chanel)
                result['Subdivision'].append(sub)
                result['GoodsKey'].append(goods)
                result['Percent_'].append(value)
        df_res = pd.DataFrame(result)
        for i, row in tqdm(df_res.iterrows()):
            chanel, sub, goods, percent = row
            sql_insert = f"""   
                INSERT INTO DataWH.planning.PercentSubdivision(
                 Chanel 
	             ,Subdivision 
	            ,GoodsKey 
	            ,Percent_ 
                )
                VALUES('{chanel}','{sub}','{goods}',{percent})
            """

            conn.execute(sql_insert)
            cursor.commit()


class PercentSubdivisionSeason(models.Model):
    id = models.AutoField(primary_key=True)
    opition_plan = models.ForeignKey(OptionPlanRef, on_delete=models.CASCADE)
    file = models.FileField(null=True, upload_to='excel')
    sheet_name = models.CharField(max_length=255, default='', null=True)
    period = models.ForeignKey(PeriodPlanRef, on_delete=models.CASCADE, null=True)
    year = models.IntegerField(default=2026, null=True)

    @classmethod
    def create_by_file(cls, header):
        sheet_name = header.sheet_name
        file = header.file
        conn, cursor = connect_database('vm-dwh', 'DataWH')

        df_in = pd.read_excel(file, sheet_name=sheet_name)

        result = {
            'Subdivision': []
            , 'MonthInt': []
            , 'GoodsKey': []
            , 'Percent_': []

        }

        print(df_in.shape)

        sql_truncate = """
            TRUNCATE TABLE DataWH.planning.PercentSeasonSubdivision
            """
        conn.execute(sql_truncate)
        cursor.commit()
        subdivision_list = df_in.columns[2:]
        for i, row in tqdm(df_in.iterrows()):
            subdivision, goods = row[0], row[1]
            for j, sub in enumerate(row[2:]):
                result['Subdivision'].append(subdivision)
                result['MonthInt'].append(j + 1)
                result['GoodsKey'].append(goods)
                result['Percent_'].append(sub)
        df_res = pd.DataFrame(result)
        for i, row in tqdm(df_res.iterrows()):
            chanel, sub, goods, percent = row
            sql_insert = f"""   
                    INSERT INTO DataWH.planning.PercentSeasonSubdivision(
                    Subdivision 
                    ,MonthInt
    	            ,GoodsKey 
    	            ,Percent_ 
                    )
                    VALUES('{chanel}','{sub}','{goods}',{percent})
                """

            conn.execute(sql_insert)
            cursor.commit()
