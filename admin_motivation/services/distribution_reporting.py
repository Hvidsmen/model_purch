"""Join final allocations to the matching calculation, never to product slices."""
from decimal import Decimal
from ..models import PremiumDistribution
from .premium_distribution import cash


def apply(rows,execution,kind,values,periods,*,product_filtered=False):
    periods=[str(p) for p in periods]
    selected=[r for r in rows if r['kind']==kind]
    for row in selected:
        row.update(distributed=None,distributed_bonus=None,distributed_total=None)
    if product_filtered or kind not in ('Продажи','Политики','Директора'):return
    batches=PremiumDistribution.objects.filter(state='completed',year=int(execution.parameters['fact_year']),currency=values['Currency'],period__in=[int(p) for p in periods]).select_related('subdivision')
    branches=values.get('Subdivision') or [];authors=values.get('DealAuthor') or []
    if isinstance(authors,str):authors=[authors]
    if isinstance(branches,str):branches=[branches]
    if branches:batches=batches.filter(subdivision__subdivision_key__in=branches)
    for batch in batches:
        code=str(batch.period);branch=batch.subdivision.subdivision_key
        if not any(f['kind']==kind for f in batch.funds):continue
        names={r['author'] for r in selected if r['subdivision']==branch and r['code']==code}
        names.update(r['author'] for r in batch.rows if r['kind']==kind and r['author'])
        for name in names:
            if authors and name not in authors:continue
            existing=[r for r in selected if r['subdivision']==branch and r['author']==name and r['code']==code]
            if len(existing)>1:continue # Ambiguous identities must not duplicate an allocation.
            if existing:row=existing[0]
            else:
                row={'subdivision':branch,'author':name,'kind':kind,'code':code,'period':'Год' if code=='5' else code+' кв.',
                     'amount':Decimal(0),'bonus':Decimal(0),'total':Decimal(0),'possible':Decimal(0),'currency':values['Currency'],'unallocated':False}
                rows.append(row);selected.append(row)
            payments=[r for r in batch.rows if r['kind']==kind and r['author']==name]
            payment='annual' if code=='5' else 'quarter'
            row['distributed']=sum((cash(r['amount']) for r in payments if r['payment']==payment),Decimal(0))
            row['distributed_bonus']=sum((cash(r['amount']) for r in payments if r['payment']=='catchup'),Decimal(0))
            row['distributed_total']=row['distributed']+row['distributed_bonus']
            row['distribution_revision']=batch.revision
            row['distribution_source']=batch.source.get('run_id','')
            row['distribution_id']=batch.pk


def total(rows):
    eligible=[r for r in rows if r['kind'] in ('Продажи','Политики','Директора')]
    if not eligible or any(r.get('distributed_total') is None for r in eligible):return None
    return sum((r['distributed_total'] for r in eligible),Decimal(0))


def enrich(presentation,ledger):
    rows=ledger['rows']
    presentation['distributed']=total(rows)
    for key in ('employees','registry_rows','type_totals','team_sections','period_cards'):
        for group in presentation.get(key,[]):
            members=group.get('rows')
            if members is None:
                members=[r for r in rows if (key!='period_cards' or r['code']==group['code']) and (key=='period_cards' or r['kind']==group.get('kind'))]
            group['distributed']=total(members)
            group['distributed_bonus']=sum((r.get('distributed_bonus') or Decimal(0) for r in members if r['kind'] in ('Продажи','Политики','Директора')),Decimal(0)) if group['distributed'] is not None else None


def excel_sheet(book,rows):
    from openpyxl.cell import WriteOnlyCell
    if not any('distributed_total' in r for r in rows):return
    sheet=book.create_sheet('После распределения')
    def append(values):
        cells=[]
        for value in values:
            cell=WriteOnlyCell(sheet,value=value)
            if isinstance(value,str):cell.data_type='s'
            else:cell.number_format='#,##0.00'
            cells.append(cell)
        sheet.append(cells)
    append(['Подразделение','Сотрудник','Вид мотивации','Период','Возможная премия','Премия по расчёту','После распределения','Доплата после распределения','Всего после распределения','Валюта','Версия распределения'])
    for r in rows:
        append([r['subdivision'],r['author'],r['kind'],r['period'],r.get('possible'),r['amount'],r.get('distributed') if r.get('distributed') is not None else 'Не распределено',r.get('distributed_bonus'),r.get('distributed_total'),r['currency'],r.get('distribution_revision')])
