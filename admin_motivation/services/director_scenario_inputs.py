"""Read verified source runs and recover original branches before any scenario merge."""
import json
from decimal import Decimal
from django.core.exceptions import ValidationError
from ..conns import connect_database
from .transition_2026 import MOSCOW, THERMOTRADE
from .sales_plans import number, SEGMENTS

BRANCHES=(MOSCOW,THERMOTRADE)
SOURCES={2025:('test_2025','FactTest2025'),2026:('all_2026','FactAll2026')}


def norm(value):
    return ' '.join(str(value or '').casefold().replace('ё','е').split())


def branch_name(value):
    return next((name for name in BRANCHES if norm(value)==norm(name)),None)


def read_sources(plans, connector=None):
    connection=None
    try:
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH')
        connection.timeout=60
        cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=N'PortalMotivationCalculation',@LockMode=N'Shared',@LockOwner=N'Session',@LockTimeout=0; SELECT @r;")
        row=cursor.fetchone()
        if not row or row[0]<0:raise ValidationError('Сейчас выполняется исходный расчёт. Дождитесь завершения.')
        cursor.execute("SELECT OBJECT_ID(N'motivation.PortalMotivationRun')")
        if not cursor.fetchone()[0]:raise ValidationError('Сначала выполните исходные расчёты 2025 и «Все кварталы 2026».')
        facts,metadata={},{}
        for year,(variant,table) in SOURCES.items():
            cursor.execute("SELECT TOP(1) RunId,State_,Parameters_,CompletedAt FROM motivation.PortalMotivationRun WHERE JSON_VALUE(Parameters_,'$.variant')=? ORDER BY StartedAt DESC",variant)
            run=cursor.fetchone()
            if not run or run[1]!='completed':
                raise ValidationError(f'Для директорского расчёта нужен факт {year} из завершённого «'+('Пересчёт 2025' if year==2025 else 'Все кварталы 2026')+'» с выбранным планом. Подготовка продаж сама по себе не рассчитывает денежную базу и сегменты факта.')
            params=json.loads(run[2])
            if params.get('fact_year')!=year or str(params.get('plan_version','')).strip()!=plans[year].source_version.strip():
                raise ValidationError(f'План {year} не совпадает с планом последнего исходного расчёта. Выберите тот же план или выполните исходный расчёт заново.')
            params=validated_parameters(params)
            metadata[str(year)]={'run_id':str(run[0]),'completed_at':str(run[3]),'parameters':params}
            # Fact contains one unreplicated Sales row per document/model/segment.
            # A merged display branch must never be reused as an original branch.
            cursor.execute(f"""SELECT COALESCE(s.SubdivisionName,f.[Подразделение]),f.[Квартал],
 f.[ГруппаПлановПродаж],f.[ГруппаТоваров],f.[Марка],f.[СегментСкидки],
 SUM(CASE WHEN f.[СуммаОтгрузокUSD]>0 THEN f.[СуммаОтгрузокUSD] ELSE 0 END),
 SUM(CASE WHEN f.[СуммаОтгрузокUSD]<0 THEN f.[СуммаОтгрузокUSD] ELSE 0 END),
 CASE WHEN s.SubdivisionName IS NULL THEN 1 ELSE 0 END
 FROM motivation.{table} f LEFT JOIN dbo.Subdivisions s ON s.SubdivisionKey=f.[ПодразделениеДокумент]
 WHERE f.[Год]=? AND f.[Квартал] BETWEEN 1 AND ? AND LTRIM(RTRIM(f.[ТипКоэф]))=N'Продажи' AND f.[СуммаОтгрузокUSD]<>0
 AND (s.SubdivisionName IN (?,?) OR (s.SubdivisionName IS NULL AND f.[Подразделение] IN (?,?)))
 GROUP BY s.SubdivisionName,f.[Подразделение],f.[Квартал],f.[ГруппаПлановПродаж],f.[ГруппаТоваров],f.[Марка],f.[СегментСкидки]""",year,4 if year==2025 else 3,*BRANCHES,*BRANCHES)
            values=[]
            for sub,quarter,pg,goods,brand,segment,positive,negative,missing in cursor.fetchall():
                if missing:raise ValidationError('Не удалось восстановить исходный филиал по подразделению документа; раздельный расчёт остановлен.')
                segment=str(segment or '').strip().upper().replace('О','O').replace('K','O')
                if segment not in SEGMENTS:raise ValidationError('В исходном факте неизвестный сегмент скидки O0–O4.')
                values.append({'branch':branch_name(sub),'quarter':int(quarter),'planning':pg or '*',
                    'goods':goods or '*','brand':brand or '*','segment':segment,
                    'positive':str(number(positive or 0)),'negative':str(number(negative or 0))})
            if not values:raise ValidationError(f'В исходном факте {year} нет строк Москвы/ТТ. Результат не подменяется нулём.')
            facts[year]=values
        return facts,metadata
    finally:
        if connection is not None:connection.close()


def validated_parameters(params):
    result=dict(params)
    quarter=number(params.get('quarter_share'));annual=number(params.get('year_share'))
    threshold=number(params.get('payout_threshold'))
    if not 0<=quarter<=100 or not 0<=annual<=100 or quarter+annual!=100 or not 0<=threshold<=1:
        raise ValidationError('В журнале исходного расчёта неверные доли или порог выплаты.')
    return result


def plan_inputs(scenario,year):
    result=[]
    for day,sub,pg,goods,brand,amount,segments in scenario.lines.filter(plan_date__year=year).exclude(subdivision='').order_by().values_list('plan_date','subdivision','planning_group_sales','group','brand','amount_usd','segment_amounts').iterator(chunk_size=2000):
        sub=branch_name(sub)
        if not sub or (year==2026 and day.month>9):continue
        if not isinstance(segments,dict) or set(SEGMENTS)-set(segments):
            raise ValidationError(f'В плане {year} не заполнены O0–O4. Перезагрузите план.')
        amounts=[number(segments[key]) for key in SEGMENTS];total=number(amount)
        if abs(sum(amounts,Decimal(0))-total)>max(Decimal('.0001'),abs(total)*Decimal('.00000001')):
            raise ValidationError(f'Сумма O0–O4 не совпадает с планом {year}.')
        for key,value in zip(SEGMENTS,amounts):
            result.append({'branch':sub,'quarter':(day.month-1)//3+1,'planning':pg,'goods':goods,'brand':brand,
                'segment':key,'positive':str(max(value,0)),'negative':str(min(value,0))})
    for sub in BRANCHES:
        if not any(row['branch']==sub for row in result):
            raise ValidationError(f'В выбранном плане {year} нет исходного раздельного плана «{sub}». Объединённый план нельзя автоматически разделить.')
    return result
