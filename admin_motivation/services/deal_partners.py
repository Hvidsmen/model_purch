"""Partner grouping shared by native and frozen closed-deal reports."""
from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.template.loader import render_to_string

AMOUNTS = {'shipments':'СуммаОтгрузокUSD','quarterly':'СуммаКвМотивацииUSD','annual':'СуммаМотивацииUSDГод'}


def mode(query):
    value=query.get('deal_view','table')
    if value not in ('table','partners'):
        raise ValidationError('Выберите вид отчёта: простая таблица или группировка по партнёрам.')
    return value


def paginate(rows, data, page):
    partners=defaultdict(lambda: dict(count=0,shipments=Decimal(0),quarterly=Decimal(0),annual=Decimal(0)))
    for row in rows:
        result=partners[row.get('Партнер') or ''];result['count']+=1
        for key,column in AMOUNTS.items():
            result[key]+=Decimal(str(row.get(column) or 0))
    items=[dict(partner=key,**values,possible=values['quarterly']+values['annual']) for key,values in sorted(partners.items())]
    selected=Paginator(items,50).get_page(page)
    data.update(deal_view='partners',partner_groups=list(selected),partner_count=len(items),details=[],
                page=selected.number,pages=selected.paginator.num_pages,offset=(selected.number-1)*50)
    return data


def decorate(data,query):
    data.setdefault('deal_view','table')
    for view in ('table','partners'):
        target=query.copy();target['deal_view']=view;target['run']='1'
        for key in ('page','export','_partner','_partner_rows'):
            target.pop(key,None)
        data[view+'_url']='?'+target.urlencode()
    for group in data.get('partner_groups',[]):
        group['title']=group.get('partner') or 'Без партнёра'
        target=query.copy();target['deal_view']='table';target['_partner_rows']='1';target['_partner']=group.get('partner') or '';target['run']='1';target['page']='1';target.pop('export',None)
        group['details_url']='?'+target.urlencode()
    target=query.copy();target['export']='csv';target['run']='1'
    data['csv_url']='?'+target.urlencode()


def response(request,data,errors):
    if errors or data is None:
        return JsonResponse({'errors':errors or ['Выберите подразделение и вид мотивации.']},status=400)
    return JsonResponse({'html':render_to_string('admin_motivation/reports_next/_deal_partner_rows.html',{'data':data},request=request)})
