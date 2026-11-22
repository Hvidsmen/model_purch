"""Read-only ERP price snapshots. Resolve real column names before building SQL."""
from collections import defaultdict
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from django.core.exceptions import ValidationError
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

def text(alias,key):return 'CAST('+alias+'.'+quoted(key)+' AS nvarchar(500))' if key else 'NULL'


def build_sql(schema):
    n=schema.get(GOODS,{});p=schema.get(PRICES,{})
    ref=column(n,'Ссылка',required=True);name=column(n,'Наименование',required=True)
    model=column(p,'НоменклатураСсылка','Номенклатура',required=True)
    kind=column(p,'ВидЦеныСсылка','ВидЦенСсылка','ТипЦенСсылка','ВидЦены','ВидЦен','ТипЦен',required=True)
    kind_text=column(p,'ВидЦены','ВидЦен','ТипЦен') or kind
    joins='';kind_name=text('p',kind_text)
    for table in KINDS:
        c=schema.get(table,{})
        if 'Ссылка' in c and 'Наименование' in c:
            joins+=' LEFT JOIN DataWH.erp.'+quoted(table)+' k ON '+text('k','Ссылка')+'='+text('p',kind)
            kind_name='COALESCE('+text('k','Наименование')+','+kind_name+')';break
    currency=column(p,'ВалютаСсылка','Валюта')
    currency_name=text('p',column(p,'Валюта') or currency)
    if currency:
        for table in CURRENCY_TABLES:
            c=schema.get(table,{})
            code=column(c,'БуквенныйКод','КодISO','Код','Наименование')
            if 'Ссылка' in c and code:
                joins+=' LEFT JOIN DataWH.erp.'+quoted(table)+' c ON '+text('c','Ссылка')+'='+text('p',currency)
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
    sql='''WITH prices AS (SELECT '''+text('p',model)+''' model_ref,'''+text('p',kind)+''' kind_ref,
 '''+kind_name+''' kind_name,'''+currency_name+''' currency,p.'''+quoted(price)+''' price,p.'''+quoted(period)+''' price_at,
 '''+text('p',unit)+''' price_unit,'''+text('p',characteristic)+''' characteristic,
 DENSE_RANK() OVER(PARTITION BY p.'''+quoted(model)+''',p.'''+quoted(kind)+''' ORDER BY p.'''+quoted(period)+''' DESC) latest
 FROM DataWH.erp.'''+quoted(PRICES)+''' p '''+joins+'''
 WHERE p.'''+quoted(period)+''' < ? AND '''+kind_name+''' LIKE ?)
 SELECT TOP (50001) '''+text('n',ref)+','+text('n',name)+','+text('n',group)+','+text('n',brand)+','+volume_sql+','+weight_sql+''',x.kind_ref,x.kind_name,x.currency,x.price,x.price_at,x.price_unit,x.characteristic
 FROM DataWH.erp.'''+quoted(GOODS)+''' n LEFT JOIN prices x ON x.model_ref='''+text('n',ref)+''' AND x.latest=1'''+(' WHERE '+' AND '.join(predicates) if predicates else '')
    return sql


def load_prices(as_of):
    connection=cursor=None
    try:
        end=as_of+timedelta(days=1)
        connection,cursor=connect_database('vm-dwh','DataWH');connection.timeout=90
        cursor.execute('SELECT TABLE_NAME,COLUMN_NAME,DATA_TYPE FROM DataWH.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME IN ('+','.join('?' for _ in TABLES)+')','erp',*TABLES)
        schema=defaultdict(dict)
        for table,name,kind in cursor.fetchall():schema[table][name]=kind
        sql=build_sql(schema);cursor.execute(sql,end,'PURCH%');raw=cursor.fetchall()
        if len(raw)>50000:raise ValidationError('Источник вернул больше 50 000 вариантов. Нужен более узкий отбор источника.')
        return records(raw)
    except ValidationError:raise
    except Exception:raise ValidationError('Не удалось загрузить цены PURCH из DataWH. Проверьте подключение и схему регистра; сохранённые данные не изменены.')
    finally:
        try:
            if cursor is not None:cursor.close()
        finally:
            if connection is not None:connection.close()


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
        if characteristic and characteristic not in ('00000000-0000-0000-0000-000000000000','0'):
            data['source_data']['error']='Цена задана для характеристики номенклатуры. Требуется уточнить отбор источника.'
        if unit:data['source_data']['unit_warning']='Проверьте, что цена задана за одну единицу модели: '+str(unit)
        key=(data['model_ref'],data['price_type_ref'],currency)
        if key in grouped and (grouped[key]['price'],grouped[key]['price_at'],grouped[key]['source_data'])!=(data['price'],data['price_at'],data['source_data']):
            grouped[key]['source_data']['error']='Несколько различных цен или единиц на последнюю дату. Уточните отбор регистра.';grouped[key]['price']=None
        elif key not in grouped:grouped[key]=data
    return list(grouped.values())
