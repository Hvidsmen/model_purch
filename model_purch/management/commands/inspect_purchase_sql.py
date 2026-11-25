"""Read corporate purchasing object definitions without changing SQL Server."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from model_purch.conns import connect_database

class Command(BaseCommand):
    help='Сохранить определения SQL-объектов модели закупок для перехода на PlanningKey (только чтение).'
    def add_arguments(self,parser):parser.add_argument('--output',default='.local/purchase-sql-objects.json')
    def handle(self,*args,**options):
        connection,cursor=connect_database('vm-dwh','ModelPurch')
        try:
            connection.timeout=60
            cursor.execute("""SELECT s.name,o.name,o.type_desc,m.definition
                FROM sys.objects o JOIN sys.schemas s ON o.schema_id=s.schema_id
                LEFT JOIN sys.sql_modules m ON m.object_id=o.object_id
                WHERE (s.name=N'dbo' AND o.name IN (N'sp_ETLDataBase',N'sp_CreateTableModel',N'sp_Date'))
                   OR (s.name=N'portal' AND o.name IN (N'vPGGoods_Lag',N'vPGGoods'))
                ORDER BY s.name,o.name""")
            objects=[{'schema':s,'name':n,'type':t,'definition':d} for s,n,t,d in cursor.fetchall()]
            cursor.execute("""SELECT s.name,o.name,c.name,t.name,c.max_length,c.is_nullable
                FROM sys.columns c JOIN sys.objects o ON o.object_id=c.object_id
                JOIN sys.schemas s ON o.schema_id=s.schema_id JOIN sys.types t ON c.user_type_id=t.user_type_id
                WHERE (s.name=N'dbo' AND o.name IN (N'TableForCalcPlanIncomes',N'PlanIncome'))
                   OR (s.name=N'portal' AND o.name IN (N'PGGoods',N'vPGGoods_Lag'))
                ORDER BY s.name,o.name,c.column_id""")
            columns=[{'schema':s,'object':o,'column':c,'type':t,'length':l,'nullable':bool(n)} for s,o,c,t,l,n in cursor.fetchall()]
        finally:connection.close()
        if not objects:raise CommandError('Объекты не найдены или нет доступа к их определениям. Нужны права VIEW DEFINITION.')
        output=Path(options['output']);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps({'objects':objects,'columns':columns},ensure_ascii=False,indent=2),encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Сохранено: {output.resolve()}. SQL Server не изменён.'))
        if any(not obj['definition'] for obj in objects):self.stderr.write('Часть определений недоступна; проверьте права VIEW DEFINITION.')
