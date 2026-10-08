def execute_sql(sql, connector):
    connection, cursor = connector('vm-dwh', 'ModelPurch')
    try:
        cursor.execute(sql)
        # Consume every result set so errors in later statements are raised too.
        while cursor.nextset():
            pass
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()



def run_step(order, run_id, connector):
    if order == 1:
        return {'message': 'Алгоритм успешно запущен'}
    statements = {
        2: ('EXEC [ModelPurch].[dbo].[sp_ETLDataBase]; EXEC [ModelPurch].[dbo].[sp_CreateTableModel];', 'Данные подготовлены'),
        4: ('EXEC [ModelPurch].[dbo].[sp_Date];', 'Таблицы обновлены'),
        5: ("EXEC msdb.dbo.sp_start_job 'ModelPurch';", 'Куб отправлен на обсчет'),
    }
    if order == 3:
        from ..models import AlgorithmRun
        from ..calc_purch import calc_purch
        run = AlgorithmRun.objects.get(pk=run_id)
        if run.parameters.get('scope') == 'all':
            calc_purch()
            return {'message': 'Заказ рассчитан по всем сценариям'}
        # Preserve the scope of older runs already started before this update.
        if not run.scenario_id:
            raise ValueError('Не зафиксированы параметры расчёта.')
        calc_purch(scenario_name=run.parameters['scenario']['name'])
        return {'message': 'Заказ рассчитан'}
    sql, message = statements[order]
    execute_sql(sql, connector)
    return {'message': message}
