"""Read verified 2025 Fact and prepared 2026 sales before any branch merge."""
import json
from decimal import Decimal
from django.core.exceptions import ValidationError
from ..conns import connect_database
from .transition_2026 import MOSCOW, THERMOTRADE
from .sales_plans import number, SEGMENTS
from .director_input_schema import require_columns

BRANCHES=(MOSCOW,THERMOTRADE)
SOURCES={2025:('test_2025','FactTest2025')}


def norm(value):
    return ' '.join(str(value or '').casefold().replace('ё','е').split())


def branch_name(value):
    return next((name for name in BRANCHES if norm(value)==norm(name)),None)


def read_sources(plans, version, connector=None, progress=None):
    from .director_prepared_fact import settings_for, read_fact
    settings=settings_for(plans[2026])
    connection=None
    stage='Подключение к источникам сравнения директоров'
    try:
        if progress:progress('Подключение и проверка доступности DWH',3)
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH')
        connection.timeout=60
        stage='Проверка доступности исходных данных сравнения'
        cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=N'PortalMotivationCalculation',@LockMode=N'Shared',@LockOwner=N'Session',@LockTimeout=0; SELECT @r;")
        row=cursor.fetchone()
        if not row or row[0]<0:raise ValidationError('Сейчас выполняется исходный расчёт. Дождитесь завершения.')
        cursor.execute("SELECT OBJECT_ID(N'motivation.PortalMotivationRun')")
        if not cursor.fetchone()[0]:raise ValidationError('Сначала выполните «Пересчёт 2025» с выбранным планом.')
        facts,metadata={},{}
        for year,(variant,table) in SOURCES.items():
            stage=f'Чтение исходного расчёта {year}'
            if progress:progress(stage,4)
            cursor.execute("SELECT TOP(1) RunId,State_,Parameters_,CompletedAt FROM motivation.PortalMotivationRun WHERE JSON_VALUE(Parameters_,'$.variant')=? ORDER BY StartedAt DESC",variant)
            run=cursor.fetchone()
            if not run or run[1]!='completed':
                raise ValidationError('Для варианта 2025 нужен факт из завершённого «Пересчёт 2025» с выбранным планом. Для 2026 предварительный расчёт премий не требуется.')
            params=json.loads(run[2])
            if params.get('fact_year')!=year or str(params.get('plan_version','')).strip()!=plans[year].source_version.strip():
                raise ValidationError(f'План {year} не совпадает с планом последнего исходного расчёта. Выберите тот же план или выполните исходный расчёт заново.')
            from .recalculation_2025 import RULE_VERSION
            if params.get('recalculation_rule')!=RULE_VERSION:
                raise ValidationError('Исходный пересчёт 2025 выполнен по прежним правилам. Запустите сохранённый пересчёт 2025 после обновления портала.')
            params=validated_parameters(params)
            metadata[str(year)]={'run_id':str(run[0]),'completed_at':str(run[3]),'parameters':params}
            require_columns(cursor,('motivation.FactTest2025','dbo.Subdivisions'))
            # Fact contains one unreplicated Sales row per document/model/segment.
            # A merged display branch must never be reused as an original branch.
            from .motivation_exclusions import sql_allowed
            participation=sql_allowed('Директора',group_expression='f.[ГруппаПлановПродаж]',channel_expression='s.Chanel')
            cursor.execute(f"""SELECT COALESCE(s.SubdivisionName,f.[Подразделение]),f.[Квартал],
 f.[ГруппаПлановПродаж],f.[ГруппаТоваров],f.[Марка],f.[СегментСкидки],
 SUM(CASE WHEN f.[СуммаОтгрузокUSD]>0 THEN f.[СуммаОтгрузокUSD] ELSE 0 END),
 SUM(CASE WHEN f.[СуммаОтгрузокUSD]<0 THEN f.[СуммаОтгрузокUSD] ELSE 0 END),
 CASE WHEN s.SubdivisionName IS NULL THEN 1 ELSE 0 END
 FROM motivation.{table} f LEFT JOIN dbo.Subdivisions s ON s.SubdivisionKey=f.[ПодразделениеДокумент]
 WHERE f.[Год]=? AND f.[Квартал] BETWEEN 1 AND ? AND LTRIM(RTRIM(f.[ТипКоэф]))=N'Продажи' AND f.[СуммаОтгрузокUSD]<>0
 AND {participation}
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
        stage='Чтение подготовленного факта 2026'
        if progress:progress(stage,5)
        facts[2026],metadata['2026']=read_fact(cursor,version,BRANCHES,settings)
        return facts,metadata
    except ValidationError:
        raise
    except Exception as exc:
        from .calculation_jobs import JobOperationError
        raise JobOperationError(stage,exc) from exc
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
    for day,sub,pg,goods,brand,amount,segments,sources in scenario.lines.filter(plan_date__year=year).exclude(subdivision='').order_by().values_list('plan_date','subdivision','planning_group_sales','group','brand','amount_usd','segment_amounts','source_subdivisions').iterator(chunk_size=2000):
        sub=branch_name(sub)
        if not sub or (year==2026 and day.month>9):continue
        if not isinstance(segments,dict) or set(SEGMENTS)-set(segments):
            raise ValidationError(f'В плане {year} не заполнены O0–O4. Перезагрузите план.')
        amounts=[number(segments[key]) for key in SEGMENTS];total=number(amount)
        if abs(sum(amounts,Decimal(0))-total)>max(Decimal('.0001'),abs(total)*Decimal('.00000001')):
            raise ValidationError(f'Сумма O0–O4 не совпадает с планом {year}.')
        from .motivation_exclusions import amounts_for
        amounts=amounts_for('Директора',segments,pg,sources)
        raw_goods=goods
        goods=goods if pg in ('1. RAC','2. VRF','3. PROF') else '*'
        brand=brand if raw_goods in ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') else '*'
        for key,value in zip(SEGMENTS,amounts):
            result.append({'branch':sub,'quarter':(day.month-1)//3+1,'planning':pg or '*','goods':goods or '*','brand':brand or '*',
                'segment':key,'positive':str(max(value,0)),'negative':str(min(value,0))})
    for sub in BRANCHES:
        if not any(row['branch']==sub for row in result):
            raise ValidationError(f'В выбранном плане {year} нет исходного раздельного плана «{sub}». Объединённый план нельзя автоматически разделить.')
    return result
