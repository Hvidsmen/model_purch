import pandas as pd
from .conns import connect_database

def calc_purch():
    sql_scenarion = """SELECT DISTINCT СценарийМодели FROM ModelPurch.dbo.TableForCalcPlanIncomes"""
    con, cur = connect_database('vm-dwh', 'ModelPurch')

    try:
        result = {'PlanningGroupOZP': [], 'Date_': [], 'Qty': [], 'Scenario': []}
        df_sc = pd.read_sql(sql_scenarion, con=con)
        for i, row in df_sc.iterrows():
            sc= row['СценарийМодели']
            sql_pg_list = """SELECT DISTINCT PlanningGroupOZP FROM ModelPurch.dbo.TableForCalcPlanIncomes i
            INNER JOIN ModelPurch.portal.vPGGoods_Lag g
            ON i.PlanningGroupOZP = g.planning_group
            AND i.СценарийМодели = g.scenario_name

             WHERE СценарийМодели = ?
           --  AND g.Kind_purch IN ('Закупается', 'Заказное оборудование')
             --AND PlanningGroupOZP = 'Kent Spl (o/f-0) (Kumo) [MRAC]'
             """

            df_pg = pd.read_sql(sql_pg_list, con=con, params=[sc])

            for i, row in df_pg.iterrows():
                pg = row['PlanningGroupOZP']
                sql_data_all = """

                SELECT [PlanningGroupOZP]
                      ,[CoeffEnd]
                      ,IIF(SUM([Количество])<SUM([ПланПродаж]*[CoeffEnd]),SUM([ПланПродаж]*[CoeffEnd])-SUM([Количество]),0) QtyALLOrder
                  FROM [ModelPurch].[dbo].[TableForCalcPlanIncomes]
                  WHERE
                    [PlanningGroupOZP] = ?
                    AND СценарийМодели = ?
                    AND Date_ = (SELECT MAX(Date_) FROM [ModelPurch].[dbo].[TableForCalcPlanIncomes] WHERE СценарийМодели = ?)
                GROUP BY
                    [PlanningGroupOZP]
                      ,[CoeffEnd]
                """
                df_all_ord = pd.read_sql(sql_data_all, con=con, params=[pg, sc, sc])
                qty_all_ord = sum([row['QtyALLOrder'] for _,row in df_all_ord.iterrows()])
                sql_by_date = """
                    SELECT  [Date_]
                          ,[Количество]
                          ,[ПланСтока]
                          ,[PlanningGroupOZP]
                          ,[CoeffEnd]
                          ,[ПланПродаж]
                      FROM [ModelPurch].[dbo].[TableForCalcPlanIncomes]
                      WHERE
                        [PlanningGroupOZP] = ?
                        AND СценарийМодели = ?
                    ORDER BY
                        [Date_]

                """
                df_by_date = pd.read_sql(sql_by_date, con=con, params=[pg, sc])
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

                    result['PlanningGroupOZP'].append(pg)
                    result['Date_'].append(date_row)
                    result['Qty'].append(qty_in)
                    result['Scenario'].append(sc)

                if qty_all_ord != 0 and date_end is None:
                    raise ValueError(f'Нет дат для расчёта группы {pg} в сценарии {sc}')
                if qty_all_ord != 0:
                    result['PlanningGroupOZP'].append(pg)
                    result['Date_'].append(date_end)
                    result['Qty'].append(qty_all_ord)
                    result['Scenario'].append(sc)

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
        # 4. Вставка БЕЗ fast_executemany (старый драйвер может глючить)
        if rows:
            cur.executemany(
                "INSERT INTO [dbo].[PlanIncome] ([PlanningGroupOZP], [Date_], [Qty], [Scenario]) VALUES (?, ?, ?, ?)",
                rows,
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
