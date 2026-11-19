"""Allocate the Q4 100% scenario using frozen Q4-2026 author shipment shares."""
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from django.core.exceptions import ValidationError
from .coefficient_iteration_calculation import MOSCOW, TT, MERGED, ZERO, branch, key, empty_row
from .coefficient_iteration import norm
from .sales_plans import number
from django.utils import timezone
from .subdivision_scope import included
from .motivation_exclusions import allowed
from .iteration_live_sales_authors import LABEL

RULE='q4-2026-author-plan-shares-v3-live-sales'
SQL="""SELECT sub.SubdivisionName,
 COALESCE(NULLIF(LTRIM(RTRIM(s.Даичи_АвторСделкиНаименование)),N''),N''),
 COALESCE(NULLIF(LTRIM(RTRIM(p.PlanningGroupSalesErp)),N''),N'9. OTHER'),
 CASE WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN p.GroupERP ELSE N'' END,
 CASE WHEN p.GroupERP IN (N'Бытовые кондиционеры',N'Полупромышленные кондиционеры',N'Системы мульти-сплит',N'Системы VRF') THEN p.BrandName ELSE N'' END,
 sub.Chanel,SUM(s.Price-s.AmountUSDIt_ITW+s.BallsOuterUSD),
 SUM(CASE WHEN s.Price-s.AmountUSDIt_ITW+s.BallsOuterUSD IS NULL THEN 1 ELSE 0 END)
 FROM DataWH.dbo.Sales s
 INNER JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey=s.Subdivision
 INNER JOIN DataWH.dbo.Product p ON p.ProductCode=s.ProductCode AND p.[Тип номенклатуры]=N'Товар'
 INNER JOIN DataWH.dbo.Customer c ON c.CustomerKey=s.CustomerKey
 WHERE s.ClosedDealDate>=? AND s.ClosedDealDate<? AND sub.SubdivisionName IN (__BRANCHES__)
 GROUP BY sub.SubdivisionName,
 COALESCE(NULLIF(LTRIM(RTRIM(s.Даичи_АвторСделкиНаименование)),N''),N''),
 COALESCE(NULLIF(LTRIM(RTRIM(p.PlanningGroupSalesErp)),N''),N'9. OTHER'),
 CASE WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN p.GroupERP ELSE N'' END,
 CASE WHEN p.GroupERP IN (N'Бытовые кондиционеры',N'Полупромышленные кондиционеры',N'Системы мульти-сплит',N'Системы VRF') THEN p.BrandName ELSE N'' END,sub.Chanel"""


def read_fact(cursor,rates,through):
    if through < date(2026,10,1):raise ValidationError('Для долей авторов ещё нет периода IV квартала 2026.')
    names=sorted({name for name in rates.names.values() if rates.role(name) in (MOSCOW,TT)})
    if not names:raise ValidationError('Не найдены Москва и ТТ для факта авторов.')
    from datetime import timedelta
    end=min(date(2027,1,1),through+timedelta(days=1))
    cursor.execute(SQL.replace('__BRANCHES__',','.join('?' for _ in names)),date(2026,10,1),end,*names)
    facts=[]
    for sub,author,p,g,b,channel,amount,missing in cursor.fetchall():
        if not included(sub):continue
        if missing or amount is None:raise ValidationError('В факте IV квартала 2026 есть сделки без суммы. Доли авторов не рассчитаны.')
        if rates.role(sub) not in (MOSCOW,TT):raise ValidationError('Источник вернул подразделение вне Москвы и ТТ.')
        facts.append({'branch':branch(sub),'author':str(author or '').strip(),'planning':p or '*','goods':g or '*','brand':b or '*','channel':channel or '', 'amount':str(number(amount))})
    return facts,{'rule':RULE,'source_table':'DataWH.dbo.Sales','author_field':'Даичи_АвторСделкиНаименование',
                  'loaded_at':timezone.now().isoformat(),'from':'2026-10-01','through':str(min(through,date(2026,12,31))),'rows':len(facts),
                  'author_source':LABEL,
                  'named_authors':len({norm(fact['author']) for fact in facts if fact['author'] and allowed('Политики',fact['planning'],fact['channel'])}),
                  'missing_author_rows':sum(not fact['author'] and allowed('Политики',fact['planning'],fact['channel']) for fact in facts)}


def split_value(value,weights):
    value=number(value);total=sum(weights,ZERO)
    with localcontext() as context:
        context.prec=64
        parts=[value*weight/total for weight in weights[:-1]]
        return [*parts,value-sum(parts,ZERO)]


def allocate(rows,details,facts,rates):
    with localcontext() as context:
        context.prec=64
        return _allocate(rows,details,facts,rates)


def _allocate(rows,details,facts,rates):
    grids={};labels={}
    for kind in ('Политики',):
        grid=defaultdict(lambda:defaultdict(Decimal))
        for fact in facts:
            if not allowed(kind,fact['planning'],fact.get('channel','')):continue
            dimensions=rates.classification(MOSCOW,kind,(fact['planning'],fact['goods'],fact['brand']))
            identity=norm(fact['author']) or '__missing_author__';labels.setdefault(identity,fact['author'] or 'Не распределено: автор не указан')
            grid[key(dimensions)][identity]+=number(fact['amount'])
        grids[kind]=grid
    allocated=[]
    levels=('Точная классификация','Группа планов продаж + товарная группа','Группа планов продаж','Москва + ТТ')
    for item in details:
        if item['kind']!='Политики':allocated.append(item);continue
        grid=grids[item['kind']];dims=key(rates.classification(MOSCOW,item['kind'],(item['planning'],item['goods'],item['brand'])))
        candidates=None;level='Нет базы IV квартала 2026'
        for scope in range(4):
            net=defaultdict(Decimal)
            for dimensions,authors in grid.items():
                if scope==0 and dimensions!=dims or scope==1 and dimensions[:2]!=dims[:2] or scope==2 and dimensions[0]!=dims[0]:continue
                for author,amount in authors.items():net[author]+=amount
            positive={author:amount for author,amount in net.items() if amount>ZERO}
            if positive:candidates=sorted(positive.items());level=levels[scope];break
        if not candidates:candidates=[('__no_basis__',Decimal(1))];labels['__no_basis__']='Не распределено: нет положительного факта IV кв. 2026'
        weights=[amount for _,amount in candidates]
        fields={field:split_value(item[field],weights) for field in ('plan','fact','possible','paid','annual_contribution')}
        segments=[split_value(amount,weights) for amount in item['segments']]
        for index,(identity,weight) in enumerate(candidates):
            part=deepcopy(item);part.update(employee=labels[identity],author_allocation=True,allocation_level=level,allocation_fact=str(weight) if identity!='__no_basis__' else None)
            part.update({field:parts[index] for field,parts in fields.items()});part['segments']=[parts[index] for parts in segments];allocated.append(part)
    grouped={}
    for item in allocated:
        if not item.get('author_allocation'):continue
        identity=(item['branch'],item['kind'],item['employee'])
        target=grouped.setdefault(identity,empty_row(item['scenario'],item['year'],item['branch'],item['kind'],item['employee']))
        target['allocated_plan']=target.get('allocated_plan',ZERO)+item['plan']
        q=item['quarter']-1
        target['quarters'][q]+=item['paid'];target['possible'][q]+=item['possible'];target['annual_quarters'][q]+=item['annual_contribution']
    for item in grouped.values():
        item['annual_accrued']=sum(item['annual_quarters'],ZERO);item['annual_paid']=item['annual_accrued'];item['total']=sum(item['quarters'],ZERO)+item['annual_paid']
    return [*grouped.values(),*[row for row in rows if row['kind']!='Политики']],allocated
