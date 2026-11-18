"""Read-only physical block stock/incomes using the agreed ModelPurch rules."""
from copy import deepcopy
from datetime import date,datetime
from zoneinfo import ZoneInfo
import logging
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_purch.conns import connect_database
from .sales_planning_scope import PRODUCT_GROUP
from .sales_planning_source import reference_key
from .sales_planning_group_service import block_kind
from .split_engine import number,stored,ZERO

# Keep ModelCodeERP membership, but neither multiply quantities by its
# outdoor-only accounting flag nor multiply rows by duplicate model mappings.
SCOPE = ''';WITH goods AS (
 SELECT DISTINCT n.Ссылка,n.Наименование,n.БИТ_КлассМощности,n.БИТ_ТипБлока
 FROM DataWH.erp.[Справочники.Номенклатура] n
 WHERE LTRIM(RTRIM(n.Родитель))=? AND LTRIM(RTRIM(n.Даичи_ГруппаПланирования))=?
 AND EXISTS(SELECT 1 FROM DataWH.dbo.ModelCodeERP m WHERE m.ModelCode=n.Наименование)
), raw AS (
 SELECT sg.ReportDate,sg.НоменклатураСсылка,sg.ВНаличии,sg.ВПути,sg.КПоступлению,sg.ТипПоступления,sg.ДатаПоступления,
        n.Наименование,n.БИТ_КлассМощности,n.БИТ_ТипБлока,store.даичи_НазначениеСклада
 FROM DataWH.erp.StreamGoods sg
 INNER JOIN goods n ON sg.НоменклатураСсылка=n.Ссылка
 INNER JOIN DataWH.erp.[Справочник.Склады] store ON store.Ссылка=sg.СкладСсылка
 WHERE sg.ReportDate=?
)'''
STOCK_SQL=SCOPE+'''
 SELECT НоменклатураСсылка,Наименование,БИТ_КлассМощности,БИТ_ТипБлока,
        SUM(ВНаличии+ВПути) AS Quantity,
        SUM(CASE WHEN ВНаличии IS NULL OR ВПути IS NULL THEN 1 ELSE 0 END) AS UnknownQuantity
 FROM raw WHERE даичи_НазначениеСклада=N'Продажа'
 GROUP BY НоменклатураСсылка,Наименование,БИТ_КлассМощности,БИТ_ТипБлока
 HAVING SUM(ВНаличии+ВПути)>0 OR SUM(CASE WHEN ВНаличии IS NULL OR ВПути IS NULL THEN 1 ELSE 0 END)>0'''
INCOME_SQL=SCOPE+''', dated AS (
 SELECT *,CASE WHEN ТипПоступления=N'ЗаказПоставщику'
   THEN DATEADD(MONTH,2,IIF(ДатаПоступления<=ReportDate,DATEADD(DAY,14,ReportDate),ДатаПоступления))
   ELSE IIF(ДатаПоступления<=ReportDate,DATEADD(DAY,14,ReportDate),ДатаПоступления) END AS ExpectedDate
 FROM raw WHERE ТипПоступления IN (N'ЗаказПоставщику',N'ПриобретениеТоваровУслуг')
 OR (ТипПоступления=N'ПеремещениеТоваров' AND даичи_НазначениеСклада<>N'Продажа')
)
 SELECT НоменклатураСсылка,Наименование,БИТ_КлассМощности,БИТ_ТипБлока,
        SUM(ВПути+КПоступлению) AS Quantity,
        SUM(CASE WHEN ВПути IS NULL OR КПоступлению IS NULL THEN 1 ELSE 0 END) AS UnknownQuantity,
        ExpectedDate,ТипПоступления
 FROM dated
 GROUP BY НоменклатураСсылка,Наименование,БИТ_КлассМощности,БИТ_ТипБлока,ExpectedDate,ТипПоступления
 HAVING SUM(ВПути+КПоступлению)>0 OR SUM(CASE WHEN ВПути IS NULL OR КПоступлению IS NULL THEN 1 ELSE 0 END)>0'''
SNAPSHOT_SQL='SELECT COUNT_BIG(*) FROM DataWH.erp.StreamGoods WHERE ReportDate=?'


def source_rows(scenario,group):
    connection=cursor=None
    try:
        connection,cursor=connect_database('vm-dwh','DataWH');connection.timeout=60
        cursor.execute(SNAPSHOT_SQL,str(scenario.stock_date));snapshot=cursor.fetchone()
        if not snapshot or not snapshot[0]:raise ValidationError('В StreamGoods нет среза на '+scenario.stock_date.strftime('%d.%m.%Y')+'. Остатки и приходы не изменены.')
        args=(PRODUCT_GROUP,group['ref']['name'],str(scenario.stock_date))
        cursor.execute(STOCK_SQL,*args);stock=cursor.fetchall()
        cursor.execute(INCOME_SQL,*args);incomes=cursor.fetchall()
        return stock,incomes
    except ValidationError:raise
    except Exception:
        logging.getLogger(__name__).exception('Split physical inventory import failed')
        raise ValidationError('Не удалось загрузить остатки и приходы из DataWH. Данные не изменены; подробности в журнале сервера.') from None
    finally:
        try:
            if cursor is not None:cursor.close()
        finally:
            if connection is not None:connection.close()


def inventory_state(scenario,gid):
    if scenario.status!='draft':raise ValidationError('Утверждённый сценарий не изменяется. Создайте корректировку.')
    if gid not in scenario.state['groups']:raise ValidationError('Выберите группу сценария.')
    state=deepcopy(scenario.state);group=state['groups'][gid];models={}
    if not group['powers']:raise ValidationError('В группе нет мощностей. Обновите справочники и снимок состава.')
    for power,values in group['powers'].items():
        if not values.get('active',True):continue
        for model in values['models']:
            key=reference_key(model['key']).casefold()
            target=(power,model['kind'])
            if key in models and models[key]!=target:raise ValidationError('Модель неоднозначно связана с мощностью. Обновите снимок состава.')
            models[key]=target
        for field in ('stock','prior','incoming'):
            for kind in ('indoor','outdoor'):values[field+'_'+kind]='0'
    if not models:raise ValidationError('В группе нет моделей для загрузки.')
    stock,incomes=source_rows(scenario,group)
    outside=ZERO
    for field,rows in [('stock',stock),('income',incomes)]:
        for row in rows:
            key,name,power,kind,quantity,unknown=row[:6]
            if unknown or quantity is None:raise ValidationError('В источнике есть незаполненные количества: '+str(name)+'. Данные не изменены.')
            quantity=number(quantity)
            target=models.get(reference_key(key).casefold())
            if target is None or target!=(str(power or '').strip(),block_kind(kind)):
                raise ValidationError('Модель '+str(name)+' отсутствует в снимке группы или изменила мощность/тип блока. Обновите справочники и снимок состава; данные не изменены.')
            pkey,block=target;destination=field
            if field=='income':
                expected=row[6]
                if isinstance(expected,datetime):expected=expected.date()
                if not isinstance(expected,date):raise ValidationError('Нет ожидаемой даты прихода: '+str(name)+'. Данные не изменены.')
                if expected<=scenario.stock_date:raise ValidationError('Дата прихода не позже даты стока. Проверьте перенос дат в источнике.')
                if expected<scenario.start:destination='prior'
                elif expected<=scenario.end:destination='incoming'
                else:outside+=quantity;continue
            cell=destination+'_'+block
            group['powers'][pkey][cell]=stored(number(number(group['powers'][pkey][cell])+quantity))
    loaded_at=timezone.now()
    group['inventory_source']={'loaded_at':loaded_at.isoformat(),'loaded_at_display':loaded_at.astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M:%S МСК'),'stock_date':str(scenario.stock_date),
        'source':'DataWH.erp.StreamGoods','quantity_basis':'physical_blocks','stock_models':len(stock),'income_rows':len(incomes),
        'after_period':stored(outside),'manual_adjusted':False}
    return state
