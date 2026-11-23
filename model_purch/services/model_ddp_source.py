"""Read-only ERP price snapshots. Resolve real column names before building SQL."""
from collections import defaultdict
import logging
import re
import traceback
from uuid import uuid4
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from django.core.exceptions import ValidationError
from django.conf import settings
from django.utils import timezone
from ..conns import connect_database

PRICES='РегистрСведений.ЦеныНоменклатуры'
GOODS='Справочники.Номенклатура'
KINDS=('Справочники.ВидыЦен','Справочники.ВидыЦенНоменклатуры','Справочник.ВидыЦен')
CURRENCY_TABLES=('Справочники.Валюты','Справочник.Валюты')
TABLES=(PRICES,GOODS,*KINDS,*CURRENCY_TABLES)
ALIASES={'usd':'USD','840':'USD','доллар сша':'USD','доллар':'USD','eur':'EUR','978':'EUR','евро':'EUR','cny':'CNY','156':'CNY','юань':'CNY','китайский юань':'CNY','руб':'RUB','rub':'RUB','rur':'RUB','643':'RUB','рубль':'RUB','российский рубль':'RUB'}


def quoted(value):return '['+value.replace(']',']]')+']'
def column(columns,*choices,required=False):
    found=next((key for key in choices if key in columns),None)
    if not found and required:raise ValidationError('В источнике не найдено поле: '+', '.join(choices))
    return found

def sql_text(alias,key,data_type=None):
    if not key:return 'NULL'
    source=alias+'.'+quoted(key)
    if str(data_type or '').lower() in ('binary','varbinary','image','timestamp','rowversion'):
        # ERP references are opaque bytes, never UTF-16 characters.
        return 'CONVERT(varchar(max),'+source+',2)'
    return 'CAST('+source+' AS nvarchar(500))'


def variant_limit():
    value=getattr(settings,'MODEL_DDP_MAX_PRICE_VARIANTS',250000)
    if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=1000000:
        raise ValidationError('MODEL_DDP_MAX_PRICE_VARIANTS должен быть целым числом от 1 до 1000000.')
    return value


def build_sql(schema):
    n=schema.get(GOODS,{});p=schema.get(PRICES,{})
    types={'n':n,'p':p}
    def text(alias,key):
        return sql_text(alias,key,types.get(alias,{}).get(key))
    ref=column(n,'Ссылка',required=True);name=column(n,'Наименование',required=True)
    model=column(p,'НоменклатураСсылка','Номенклатура',required=True)
    kind=column(p,'ВидЦеныСсылка','ВидЦенСсылка','ТипЦенСсылка','ВидЦены','ВидЦен','ТипЦен',required=True)
    kind_text=column(p,'ВидЦены','ВидЦен','ТипЦен') or kind
    joins='';kind_table=None;kind_name=text('p',kind_text)
    for table in KINDS:
        c=schema.get(table,{})
        if 'Ссылка' in c and 'Наименование' in c:
            types['k']=c;kind_table=table
            joins+=' LEFT JOIN DataWH.erp.'+quoted(table)+' k ON '+'k.[Ссылка]=p.'+quoted(kind)
            kind_name='COALESCE('+text('k','Наименование')+','+kind_name+')';break
    currency=column(p,'ВалютаСсылка','Валюта')
    currency_name=text('p',column(p,'Валюта') or currency)
    if currency:
        for table in CURRENCY_TABLES:
            c=schema.get(table,{})
            code=column(c,'БуквенныйКод','КодISO','Код','Наименование')
            if 'Ссылка' in c and code:
                types['c']=c
                joins+=' LEFT JOIN DataWH.erp.'+quoted(table)+' c ON '+'c.[Ссылка]=p.'+quoted(currency)
                currency_name='COALESCE('+text('c',code)+','+currency_name+')';break
    period=column(p,'Период','Дата',required=True);price=column(p,'Цена',required=True)
    group=column(n,'Даичи_ГруппаПланирования','даичи_ГруппаПланирования','ГруппаПланирования','ГруппаПланированияНаименование')
    brand=column(n,'Марка','Бренд','BrandName')
    volume=column(n,'ОбъемЧислитель','ОбъёмЧислитель','Объем','Объём','Volume')
    denominator=column(n,'ОбъемЗнаменатель','ОбъёмЗнаменатель')
    volume_sql='n.'+quoted(volume) if volume else 'NULL'
    if volume and denominator:volume_sql='CAST(n.'+quoted(volume)+' AS decimal(28,12))/NULLIF(n.'+quoted(denominator)+',0)'
    weight=column(n,'ВесЧислитель','Вес','Weight');weight_sql='n.'+quoted(weight) if weight else 'NULL'
    unit=column(p,'ЕдиницаИзмерения','Упаковка','Единица')
    characteristic=column(p,'ХарактеристикаНоменклатурыСсылка','ХарактеристикаНоменклатуры','Характеристика')
    predicates=[]
    for flag in ('ЭтоГруппа','ПометкаУдаления'):
        if flag in n and n[flag] in ('bit','int','smallint','tinyint','numeric','decimal'):predicates.append('COALESCE(n.'+quoted(flag)+',0)=0')
    if kind_table and n.get(ref)==p.get(model) and types['k'].get('Ссылка')==p.get(kind):
        # Seek by the native model + price-kind keys; never rank the full history.
        currency_join=''
        for table in CURRENCY_TABLES:
            c=schema.get(table,{})
            code=column(c,'БуквенныйКод','КодISO','Код','Наименование')
            if currency and 'Ссылка' in c and code:
                currency_join=' LEFT JOIN DataWH.erp.'+quoted(table)+' c ON c.[Ссылка]=p.'+quoted(currency)
                break
        sql='SELECT TOP ('+str(variant_limit()+1)+') '+','.join((text('n',ref),text('n',name),text('n',group),text('n',brand),volume_sql,weight_sql))+''',x.kind_ref,x.kind_name,x.currency,x.price,x.price_at,x.price_unit,x.characteristic
 FROM DataWH.erp.'''+quoted(GOODS)+''' n
 CROSS APPLY (
 SELECT '''+text('p',kind)+' kind_ref,'+text('k','Наименование')+' kind_name,'+currency_name+''' currency,
 p.'''+quoted(price)+''' price,p.'''+quoted(period)+''' price_at,'''+text('p',unit)+''' price_unit,'''+text('p',characteristic)+''' characteristic
 FROM DataWH.erp.'''+quoted(kind_table)+''' k
 CROSS APPLY (
 SELECT TOP (1) WITH TIES p.* FROM DataWH.erp.'''+quoted(PRICES)+''' p
 WHERE p.'''+quoted(model)+'''=n.'''+quoted(ref)+''' AND p.'''+quoted(kind)+'''=k.[Ссылка]
 AND p.'''+quoted(period)+''' < ? ORDER BY p.'''+quoted(period)+''' DESC
 ) p '''+currency_join+'''
 WHERE k.[Наименование] LIKE ?
 ) x'''+(' WHERE '+' AND '.join(predicates) if predicates else '')
        return sql
    sql='''WITH prices AS (SELECT '''+text('p',model)+''' model_ref,'''+text('p',kind)+''' kind_ref,
 '''+kind_name+''' kind_name,'''+currency_name+''' currency,p.'''+quoted(price)+''' price,p.'''+quoted(period)+''' price_at,
 '''+text('p',unit)+''' price_unit,'''+text('p',characteristic)+''' characteristic,
 DENSE_RANK() OVER(PARTITION BY p.'''+quoted(model)+''',p.'''+quoted(kind)+''' ORDER BY p.'''+quoted(period)+''' DESC) latest
 FROM DataWH.erp.'''+quoted(PRICES)+''' p '''+joins+'''
 WHERE p.'''+quoted(period)+''' < ? AND '''+('k.[Наименование]' if kind_table else 'p.'+quoted(kind_text))+''' LIKE ?)
 SELECT TOP ('''+str(variant_limit()+1)+''') '''+text('n',ref)+','+text('n',name)+','+text('n',group)+','+text('n',brand)+','+volume_sql+','+weight_sql+''',x.kind_ref,x.kind_name,x.currency,x.price,x.price_at,x.price_unit,x.characteristic
 FROM DataWH.erp.'''+quoted(GOODS)+''' n INNER JOIN prices x ON x.model_ref='''+text('n',ref)+''' AND x.latest=1'''+(' WHERE '+' AND '.join(predicates) if predicates else '')
    return sql


logger=logging.getLogger(__name__)


def diagnostic_text(error):
    """Keep driver diagnostics useful without exposing connection credentials."""
    message=str(error)
    message=re.sub(r"(?i)\b(?:password|pwd|uid|user\s*id|username|access[_ ]?token|client[_ ]?secret|server|data\s*source|database|dsn)\s*=\s*(?:\{[^}]*\}|[^;\r\n]*)", "[скрыто]", message)
    message=re.sub('(?i)(login failed for user|пользовател[ья])\\s*[\'"][^\'"]*[\'"]', '\\1 [скрыто]', message)
    return message[:2000]


def load_prices(as_of,progress=None,ensure_index=False):
    progress=progress or (lambda stage,**data: None)
    connection=cursor=None
    stage='подключение к DataWH'
    sql=''
    schema=defaultdict(dict)
    try:
        if ensure_index:
            stage='проверка индекса DataWH'
            progress(stage)
            from django.core.management import call_command
            from io import StringIO
            class IndexOutput(StringIO):
                def write(self,message):
                    nonlocal stage
                    if 'CREATE NONCLUSTERED INDEX' in message:
                        stage='создание индекса DataWH — первый запуск может занять длительное время'
                        progress(stage)
                    elif 'Подходящий индекс уже есть' in message:
                        progress('индекс DataWH уже подготовлен')
                    elif 'Индекс DataWH создан' in message:
                        progress('индекс DataWH создан')
                    return super().write(message)
            from django.core.management.base import CommandError
            try:
                call_command('optimize_purch_prices',apply=True,stdout=IndexOutput())
            except CommandError as error:
                detail=str(error)
                unsupported=(re.search(r'\b1712\b',detail) is not None or
                             'WAIT_AT_LOW_PRIORITY' in detail and re.search(r'\b155\b',detail) is not None)
                if not unsupported:raise
                warning='SQL Server не поддерживает выбранное создание индекса ONLINE. Индекс не создан; загрузка продолжается. Для создания OFFLINE нужно отдельное окно обслуживания.'
                logger.warning('PURCH index preparation skipped: %s',diagnostic_text(error))
                progress('создание индекса пропущено',warning=warning)
            stage='подключение к DataWH'
        progress(stage)
        end=as_of+timedelta(days=1)
        connection,cursor=connect_database('vm-dwh','DataWH');connection.timeout=90
        stage='чтение схемы ERP'
        progress(stage)
        cursor.execute('SELECT TABLE_NAME,COLUMN_NAME,DATA_TYPE FROM DataWH.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME IN ('+','.join('?' for _ in TABLES)+')','erp',*TABLES)
        for table,name,kind in cursor.fetchall():schema[table][name]=kind
        stage='подготовка запроса PURCH'
        progress(stage)
        missing=[table for table in (PRICES,GOODS) if not schema.get(table)]
        if missing:raise ValidationError('Не найдены таблицы или нет доступа к их метаданным: '+', '.join(missing))
        sql=build_sql(schema)
        progress('подготовка запроса PURCH',strategy='Поиск последних цен по ключам' if 'TOP (1) WITH TIES' in sql else 'Совместимый режим: ранжирование истории PURCH')
        stage='выполнение запроса PURCH'
        progress(stage)
        cursor.execute(sql,end,'PURCH%')
        stage='чтение результата PURCH'
        progress(stage)
        raw=[]
        while True:
            chunk=cursor.fetchmany(1000)
            if not chunk:break
            raw.extend(chunk)
            progress(stage,rows=len(raw))
            if len(raw)>variant_limit():break
        if len(raw)>variant_limit():raise ValidationError(f'Число моделей/видов цены PURCH превышает лимит {variant_limit():,}. Снимок не усечён и не сохранён. Увеличьте MODEL_DDP_MAX_PRICE_VARIANTS на сервере или сузьте источник.')
        stage='обработка цен и характеристик'
        progress(stage)
        return records(raw)
    except Exception as error:
        code=uuid4().hex[:12]
        state=str(error.args[0]) if error.args and re.fullmatch(r'[A-Z0-9]{5}',str(error.args[0])) else ''
        detail=' '.join(error.messages) if isinstance(error,ValidationError) else diagnostic_text(error)
        logger.error('PURCH diagnostic=%s stage=%s date=%s SQLSTATE=%s exception=%s detail=%s schema=%s query=%s stack=%s',
                     code,stage,as_of,state,type(error).__name__,detail,dict(schema),sql,
                     ''.join(traceback.format_tb(error.__traceback__)))
        hint={'08001':'Проверьте доступность SQL Server и параметры подключения.',
              '28000':'Проверьте права учётной записи службы портала.',
              '42S02':'Таблица или представление не найдено.',
              '42S22':'В запросе указано отсутствующее поле.',
              '42000':'Проверьте права SELECT и синтаксис запроса.',
              'HYT00':'Истекло время ожидания SQL Server.',
              'IM002':'На сервере портала не найден ODBC-драйвер или DSN.'}.get(state,'')
        raise ValidationError(
            f'Не удалось загрузить цены PURCH. Этап: {stage}. '
            f'Код диагностики: {code}. '+(f'SQLSTATE: {state}. ' if state else '')+
            hint+' '+detail[:800]+
            ' Сохранённые данные не изменены. Подробности в серверном журнале по коду диагностики.'
        ) from None
    finally:
        for resource in (cursor,connection):
            if resource is not None:
                try:resource.close()
                except Exception:logger.warning('PURCH: не удалось закрыть соединение/курсор',exc_info=False)


def records(raw):
    grouped={}
    for record in raw:
        ref,name,group,brand,volume,weight,kind_ref,kind_name,currency,price,at,unit=record[:12]
        characteristic=str(record[12] or '').strip() if len(record)>12 else ''
        if not ref or not name:continue
        currency=ALIASES.get(str(currency or '').strip().casefold(),str(currency or '').strip())
        data={'model_ref':str(ref),'model_name':str(name),'planning_group':str(group or ''),'brand':str(brand or ''),
              'price_type_ref':str(kind_ref or ''),'price_type':str(kind_name or ''),'currency':currency,'price':None,'price_at':None,
              'source_data':{'volume':None,'weight':None,'unit':str(unit or ''),'price_unit_confirmed':not bool(unit)}}
        try:
            if price is not None:
                value=Decimal(str(price))
                if not value.is_finite() or value<0 or value.as_tuple().exponent< -8 or value>=Decimal('1e16'):raise ValueError
                data['price']=value
            for field,value in [('volume',volume),('weight',weight)]:
                if value is not None:
                    value=Decimal(str(value))
                    if not value.is_finite() or value<0:raise ValueError
                    data['source_data'][field]=str(value)
            if at is not None:
                if not isinstance(at,datetime):at=datetime.combine(at,datetime.min.time())
                data['price_at']=timezone.make_aware(at) if timezone.is_naive(at) else at
        except (ValueError,TypeError,InvalidOperation,OverflowError):data['source_data']['error']='Некорректная цена или характеристики в источнике.';data['price']=None
        if characteristic and characteristic not in ('00000000-0000-0000-0000-000000000000','0') and set(characteristic)!={'0'}:
            data['source_data']['error']='Цена задана для характеристики номенклатуры. Требуется уточнить отбор источника.'
        if unit:data['source_data']['unit_warning']='Проверьте, что цена задана за одну единицу модели: '+str(unit)
        key=(data['model_ref'],data['price_type_ref'],currency)
        if key in grouped and (grouped[key]['price'],grouped[key]['price_at'],grouped[key]['source_data'])!=(data['price'],data['price_at'],data['source_data']):
            grouped[key]['source_data']['error']='Несколько различных цен или единиц на последнюю дату. Уточните отбор регистра.';grouped[key]['price']=None
        elif key not in grouped:grouped[key]=data
    return list(grouped.values())
