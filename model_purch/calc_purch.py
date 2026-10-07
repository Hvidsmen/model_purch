import pandas as pd
from .conns  import *

def calc_purch():
    import warnings
    warnings.filterwarnings("ignore")

    sql_scenarion = """SELECT DISTINCT СценарийМодели FROM ModelPurch.dbo.TableForCalcPlanIncomes"""
    con, cur = connect_database('vm-dwh', 'ModelPurch')

    df_sc = pd.read_sql(sql_scenarion,con=con)
    for i, row in df_sc.iterrows():
        sc= row['СценарийМодели']
        sql_pg_list = f"""SELECT DISTINCT PlanningGroupOZP FROM ModelPurch.dbo.TableForCalcPlanIncomes i
        INNER JOIN ModelPurch.portal.vPGGoods_Lag g
        ON i.PlanningGroupOZP = g.planning_group
        AND i.СценарийМодели = g.scenario_name
         
         WHERE  СценарийМодели='{sc}'
       --  AND g.Kind_purch IN ('Закупается', 'Заказное оборудование')
         --AND PlanningGroupOZP = 'Kent Spl (o/f-0) (Kumo) [MRAC]'
         """

        con, cur = connect_database('vm-dwh', 'ModelPurch')

        df_pg = pd.read_sql(sql_pg_list,con=con)

        result = {
            'PlanningGroupOZP':[]
            ,'Date_':[]
            ,'Qty':[]
            ,'Scenario':[]
        }

        for i, row in df_pg.iterrows():
            pg = row['PlanningGroupOZP']
            print(pg)
            sql_data_all = f"""    
                
            SELECT [PlanningGroupOZP]
                  ,[CoeffEnd]
                  ,IIF(SUM([Количество])<SUM([ПланПродаж]*[CoeffEnd]),SUM([ПланПродаж]*[CoeffEnd])-SUM([Количество]),0) QtyALLOrder
              FROM [ModelPurch].[dbo].[TableForCalcPlanIncomes]
              WHERE 
                [PlanningGroupOZP] = '{pg}'
                AND СценарийМодели='{sc}'
                AND Date_ = (SELECT MAX(Date_) FROM [ModelPurch].[dbo].[TableForCalcPlanIncomes] WHERE СценарийМодели='{sc}')
            GROUP BY 
                [PlanningGroupOZP]
                  ,[CoeffEnd]
            """
            df_all_ord = pd.read_sql(sql_data_all,con=con)
            qty_all_ord = sum([row['QtyALLOrder'] for _,row in df_all_ord.iterrows()])
            sql_by_date = f"""
                SELECT  [Date_]
                      ,[Количество]
                      ,[ПланСтока]
                      ,[PlanningGroupOZP]
                      ,[CoeffEnd]
                      ,[ПланПродаж]
                  FROM [ModelPurch].[dbo].[TableForCalcPlanIncomes]
                  WHERE 
                    [PlanningGroupOZP] = '{pg}'
                    AND СценарийМодели='{sc}'
                ORDER BY 
                    [Date_]
        
            """
            df_by_date = pd.read_sql(sql_by_date,con=con)
            qty_prev=0
            date_end= None
            qty_in_plan_g = 0
            for i, row in df_by_date.iterrows():
                date_row,qty_row,qty_plan_row, pg_row, _,_ = row
                date_end = date_row
                qty_in = 0
                qty_stock_row = qty_row + qty_in_plan_g
                if qty_stock_row < qty_plan_row:
                    qty_in = qty_plan_row-qty_stock_row

                    if qty_all_ord - qty_in<0:
                        qty_in =qty_all_ord
                        qty_all_ord = 0
                    else:
                        qty_all_ord-=qty_in

                    qty_in_plan_g += qty_in

                qty_prev = qty_plan_row+qty_in
                result['PlanningGroupOZP'].append(pg)
                result['Date_'].append(date_row)
                result['Qty'].append(qty_in)
                result['Scenario'].append(sc)

            if qty_all_ord !=0:
                result['PlanningGroupOZP'].append(pg)
                result['Date_'].append(date_end)
                result['Qty'].append(qty_all_ord)
                result['Scenario'].append(sc)

    engine = create_engine('vm-dwh','ModelPurch')


    con, cur = connect_database('vm-dwh', 'ModelPurch')
    import datetime
    import numpy as np
    import numpy as np

    df_result = pd.DataFrame(result)

    # 1. Даты: конвертируем в строки формата YYYY-MM-DD
    df_result['Date_'] = pd.to_datetime(df_result['Date_']).dt.strftime('%Y-%m-%d')

    # 2. Числа и строки: конвертируем в нативные Python-типы
    df_result['Qty'] = df_result['Qty'].astype(float)
    df_result['PlanningGroupOZP'] = df_result['PlanningGroupOZP'].astype(str)
    df_result['Scenario'] = df_result['Scenario'].astype(str)

    # 3. Преобразуем в список кортежей
    rows = [
        (
            str(row[0]),           # PlanningGroupOZP
            str(row[1]),           # Date_ (строка YYYY-MM-DD)
            float(row[2]),
            row[3]# Qty
        )
        for row in df_result.to_numpy()
    ]
    cur.execute("TRUNCATE TABLE [dbo].[PlanIncome]")
    con.commit()
    # 4. Вставка БЕЗ fast_executemany (старый драйвер может глючить)
    cur.executemany(
        "INSERT INTO [dbo].[PlanIncome] ([PlanningGroupOZP], [Date_], [Qty],[Scenario]) VALUES (?, ?, ?,?)",
        rows
    )
    con.commit()