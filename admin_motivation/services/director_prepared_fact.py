"""Read an unreplicated director base from the published, enriched Sales set."""
import json
from django.core.exceptions import ValidationError
from ..models import MotivationCalculationSchedule
from .prepared_sales import RULE, SCHEMA
from .sales_plans import number

# No coefficient/export views, director roster or premium output tables occur
# here. Group by the original physical Fact identity before splitting signs:
# returns on the same invoice/model must offset the invoice's accrual base.
PROJECTION = """
WITH physical AS (
 SELECT sub.SubdivisionName AS branch, DATEPART(QUARTER,s.ClosedDealDate) AS quarter,
 p.PlanningGroupSalesErp AS planning,
 CASE WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN p.GroupERP ELSE '' END AS goods,
 CASE WHEN p.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN p.BrandName ELSE '' END AS brand,
 s.DiscountMotivation AS discount, s.ДаичиТипСкидкиНаДату AS discount_type,
 SUM(s.Price - s.AmountUSDIt_ITW + s.BallsOuterUSD) AS amount
 FROM DataWH.motivation.PortalPreparedSales s
 INNER JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey=s.Subdivision
 INNER JOIN DataWH.dbo.Product p ON p.ProductCode=s.ProductCode AND p.[Тип номенклатуры]='Товар'
 INNER JOIN DataWH.dbo.Customer c ON c.CustomerKey=s.CustomerKey
 WHERE s.ClosedDealDate>=? AND s.ClosedDealDate<? AND sub.SubdivisionName IN (?,?)
 GROUP BY sub.SubdivisionName,sub.Chanel,s.Subdivision,s.ClosedDealDate,p.ModelCode,
 s.DocumentNumber,s.DateInvoice,p.PlanningGroupSalesErp,
 CASE WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN p.GroupERP ELSE '' END,
 CASE WHEN p.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN p.BrandName ELSE '' END,
 s.DiscountMotivation,s.ДаичиТипСкидкиНаДату,s.ВидЦены,
 s.[даичи_ВидЦеныВходнаяСтоимостьНаименование],c.CustomerKeyName
), segmented AS (
 SELECT f.*,m.matches,
 CASE WHEN discount>=COALESCE(m.o4,10000) THEN 'O4'
 WHEN discount>=COALESCE(m.o3,10000) AND discount<COALESCE(m.o4,10000) THEN 'O3'
 WHEN discount>=COALESCE(m.o2,10000) AND discount<COALESCE(m.o3,10000) THEN 'O2'
 WHEN discount>=COALESCE(m.o1,10000) AND discount<COALESCE(m.o2,10000) THEN 'O1'
 ELSE 'O0' END AS segment
 FROM physical f
 OUTER APPLY (SELECT COUNT(*) AS matches,MAX(o1) AS o1,MAX(o2) AS o2,MAX(o3) AS o3,MAX(o4) AS o4
 FROM #portal_director_segments cfg
 WHERE f.discount_type LIKE CONCAT('%',cfg.type_discount,'%')) m
)
SELECT branch,quarter,planning,goods,brand,segment,
 SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),
 SUM(CASE WHEN amount<0 THEN amount ELSE 0 END),MAX(matches)
FROM segmented WHERE amount<>0
GROUP BY branch,quarter,planning,goods,brand,segment
"""


def settings_for(plan):
    jobs=list(MotivationCalculationSchedule.objects.select_for_update().filter(
        variant='main',fact_year=2026,plan_version=plan.source_version).order_by('-created_at','-pk')[:2])
    if not jobs:
        raise ValidationError('В Центре расчётов сохраните параметры основного задания 2026 с выбранным планом. Запускать расчёт премий не нужно.')
    if len(jobs)>1:
        raise ValidationError('Для выбранного плана найдено несколько основных заданий 2026. Оставьте одно подходящее задание, чтобы однозначно выбрать доли и порог.')
    job=jobs[0]
    params={key:str(number(getattr(job,key))) for key in ('quarter_share','year_share','payout_threshold')}
    q,y,t=(number(params[k]) for k in ('quarter_share','year_share','payout_threshold'))
    if not 0<=q<=100 or not 0<=y<=100 or q+y!=100 or not 0<=t<=1:
        raise ValidationError('В основном задании 2026 неверные доли или порог выплаты.')
    return {**params,'settings_job_id':job.pk,'settings_job_key':str(job.key),'settings_job_name':job.name,
            'settings_source':'Основное задание 2026','fact_year':2026,'plan_version':plan.source_version}


def read_fact(cursor,version,branches,settings):
    segments=list(version.discount_segments.order_by('type_discount').values('type_discount','o0','o1','o2','o3','o4'))
    if not segments:
        raise ValidationError('В выбранной версии модели не заполнены сегменты скидок. Загрузите их в разделе «Сегменты скидок» перед сравнением.')
    # Match the same readiness/schema contract as the ordinary consumers.
    cursor.execute("SELECT OBJECT_ID(N'motivation.PortalPreparedSalesActive'),OBJECT_ID(N'motivation.PortalPreparedSalesRun'),OBJECT_ID(N'motivation.PortalPreparedSales')")
    objects=cursor.fetchone()
    if not objects or not all(objects):
        raise ValidationError('Сначала подготовьте факт продаж с 2025 года в Центре расчётов.')
    cursor.execute(f"""SELECT r.RunId,r.CompletedAt FROM motivation.PortalPreparedSalesActive a
 JOIN motivation.PortalPreparedSalesRun r ON r.RunId=a.RunId
 WHERE a.Id=1 AND r.State_=N'completed' AND r.Rule_=? AND r.PreparedFrom<='20260101'
 AND r.PreparedTo>='20270101' AND r.SourceSchema={SCHEMA}""",RULE)
    ready=cursor.fetchone()
    if not ready:raise ValidationError('Нет совместимого готового набора продаж за 2026 год. Повторите подготовку факта в Центре расчётов.')
    segment_json=json.dumps(segments,ensure_ascii=False,default=str)
    cursor.execute("""SELECT * INTO #portal_director_segments FROM OPENJSON(?) WITH
 (type_discount nvarchar(255),o1 decimal(9,4),o2 decimal(9,4),o3 decimal(9,4),o4 decimal(9,4))""",segment_json)
    cursor.execute(PROJECTION,'20260101','20261001',*branches)
    values=[]
    for branch,quarter,planning,goods,brand,segment,positive,negative,matches in cursor.fetchall():
        if matches>1:
            raise ValidationError('В выбранной версии несколько типов скидки соответствуют одной продаже. Уточните сегменты скидок; оборот не дублируется.')
        values.append({'branch':branch,'quarter':int(quarter),'planning':planning or '*',
            'goods':goods or '*','brand':brand or '*','segment':segment,
            'positive':str(number(positive or 0)),'negative':str(number(negative or 0))})
    if not values:raise ValidationError('В подготовленном факте 2026 нет продаж Москвы/ТТ за I–III кварталы. Результат не подменяется нулём.')
    return values,{'run_id':str(ready[0]),'completed_at':str(ready[1]),'source':'Подготовленный факт продаж',
        'prepared_sales_rule':RULE,'parameters':settings,'discount_segments':json.loads(segment_json)}
