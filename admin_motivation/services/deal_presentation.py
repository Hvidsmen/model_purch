"""Shared closed-deal columns and CSV export for both report generations."""
import csv
from django.http import HttpResponse

COLUMNS = [('АвторСделки','Автор сделки','text'), ('Канал','Канал','text'), ('Подразделение','Подразделение','text'),
    ('ДатаЗакрытияСделки','Дата закрытия','date'), ('ДатаОтгрузки','Дата отгрузки','date'), ('Партнер','Партнёр','text'),
    ('Номер','Номер','text'), ('ГруппаПлановПродаж','Группа планов продаж','text'), ('ГруппаТоваров','Группа товаров','text'),
    ('Марка','Марка','text'), ('Модель','Модель','text'), ('ТипСкидки','Тип скидки','text'),
    ('ПроцентСкидки','% скидки','raw_percent'), ('СегментСкидки','Сегмент скидки','text'),
    ('КоэфициентМотивации','Коэффициент мотивации','percent'), ('СуммаОтгрузокUSD','Отгрузки, USD','money'),
    ('СуммаКвМотивацииUSD','Возможная квартальная мотивация, USD','money'), ('СуммаМотивацииUSDГод','Возможная годовая мотивация, USD','money')]


def prepare(data, query):
    data['columns'] = [{'title': title, 'type': type_, 'detail': key in {'Канал','Подразделение','ДатаОтгрузки','ГруппаПлановПродаж','ГруппаТоваров','Марка','СегментСкидки'}} for key,title,type_ in COLUMNS]
    data['display_rows'] = [{'number': data['offset']+index+1, 'cells': [{'value': row.get(key), 'type': type_, 'detail': data['columns'][i]['detail']} for i,(key,_,type_) in enumerate(COLUMNS)]} for index,row in enumerate(data['details'])]
    for page_key, number in [('previous_url', data['page']-1), ('next_url', data['page']+1)]:
        target = query.copy(); target['page']=number; target['run']='1'; target.pop('export',None)
        data[page_key] = '?' + target.urlencode()
    return data


def page_csv(data):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="closed-deals-page.csv"'
    response.write('\ufeff'); writer = csv.writer(response, delimiter=';')
    writer.writerow(['№', *[title for _,title,_ in COLUMNS]])
    for row in data['display_rows']:
        values = [cell['value'] for cell in row['cells']]
        values = ["'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value for value in values]
        writer.writerow([row['number'],*values])
    return response
