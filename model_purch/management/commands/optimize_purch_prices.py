"""Inspect/create the DataWH index required by per-model latest PURCH seeks."""
from django.core.management.base import BaseCommand, CommandError
from model_purch.conns import connect_database
from model_purch.services.model_ddp_source import PRICES, column, quoted, diagnostic_text


class Command(BaseCommand):
    help='Проверить индекс DataWH для последней цены PURCH; --apply создаёт недостающий индекс ONLINE.'

    def add_arguments(self,parser):
        parser.add_argument('--apply',action='store_true')

    def handle(self,*args,**options):
        connection=cursor=None
        try:
            connection,cursor=connect_database('vm-dwh','DataWH')
            connection.timeout=0  # Index builds can exceed the application's query timeout.
            cursor.execute("""DECLARE @result int;
EXEC @result = DataWH.sys.sp_getapplock @Resource=N'Portal_PURCH_ModelKindPeriod',
 @LockMode='Exclusive', @LockOwner='Session', @LockTimeout=0;
SELECT @result;""")
            if cursor.fetchone()[0] < 0:
                raise CommandError('Подготовка индекса DataWH уже выполняется. Повторите загрузку после её завершения.')
            cursor.execute('SELECT COLUMN_NAME,DATA_TYPE FROM DataWH.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=?','erp',PRICES)
            fields=dict(cursor.fetchall())
            model=column(fields,'НоменклатураСсылка','Номенклатура',required=True)
            kind=column(fields,'ВидЦеныСсылка','ВидЦенСсылка','ТипЦенСсылка','ВидЦены','ВидЦен','ТипЦен',required=True)
            period=column(fields,'Период','Дата',required=True)
            cursor.execute('''SELECT i.name,ic.key_ordinal,c.name,i.has_filter,i.is_disabled
FROM DataWH.sys.indexes i
JOIN DataWH.sys.tables t ON t.object_id=i.object_id
JOIN DataWH.sys.schemas s ON s.schema_id=t.schema_id
JOIN DataWH.sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
JOIN DataWH.sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
WHERE s.name=? AND t.name=? AND ic.key_ordinal>0 ORDER BY i.index_id,ic.key_ordinal''','erp',PRICES)
            indexes={}
            for name,ordinal,col,filtered,disabled in cursor.fetchall():
                if not filtered and not disabled:indexes.setdefault(name,[]).append(col)
            compatible=[name for name,keys in indexes.items() if len(keys)>=3 and set(keys[:2])=={model,kind} and keys[2]==period]
            if compatible:
                self.stdout.write('Подходящий индекс уже есть: '+', '.join(compatible));return
            include=[]
            for choices in [('Цена',),('ВалютаСсылка','Валюта'),('ЕдиницаИзмерения','Упаковка','Единица'),('ХарактеристикаНоменклатурыСсылка','ХарактеристикаНоменклатуры','Характеристика')]:
                name=column(fields,*choices)
                if name and name not in (model,kind,period) and fields[name] not in ('text','ntext','image'):include.append(name)
            sql='CREATE NONCLUSTERED INDEX [IX_Portal_PURCH_ModelKindPeriod] ON DataWH.erp.'+quoted(PRICES)+' ('+quoted(model)+','+quoted(kind)+','+quoted(period)+' DESC)'
            if include:sql+=' INCLUDE ('+','.join(quoted(c) for c in include)+')'
            sql+=' WITH (ONLINE = ON, MAXDOP = 2);'
            sql='SET LOCK_TIMEOUT 60000; '+sql
            self.stdout.write(sql)
            if not options['apply']:
                self.stdout.write('Для создания: manage.py optimize_purch_prices --apply. Для 117 млн строк нужны свободное место и журнал транзакций.');return
            cursor.execute(sql)
            while cursor.nextset():pass
            connection.commit()
            self.stdout.write(self.style.SUCCESS('Индекс DataWH создан. Повторите загрузку PURCH и проверьте фактический план/IO.'))
        except Exception as error:
            if connection is not None:
                try:connection.rollback()
                except Exception:pass
            raise CommandError(diagnostic_text(error)) from None
        finally:
            for resource in (cursor,connection):
                if resource is not None:
                    try:resource.close()
                    except Exception:pass
