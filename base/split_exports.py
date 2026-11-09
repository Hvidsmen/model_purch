from io import BytesIO, StringIO
import csv
from openpyxl import Workbook
from django.http import FileResponse, HttpResponse
from django.core.exceptions import ValidationError
from .split_engine import evaluate, number


def text_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
        return "'" + value
    return value


def workbook(scenario):
    data = evaluate(scenario)
    book = Workbook(write_only=True)
    summary = book.create_sheet('Сценарий')
    for row in [('Сценарий', scenario.title), ('Версия', scenario.version), ('Статус', scenario.status),
                ('Ревизия', scenario.revision), ('Начало', str(scenario.start)), ('Конец', str(scenario.end)),
                ('Дата остатков', str(scenario.stock_date)), ('Источники', 'План и количественные остатки: ручной ввод; исторический факт: сохранённый снимок DataWH, если загружен'),
                ('Выручка USD', data['totals']['revenue']), ('Объём комплектов', data['totals']['quantity']),
                ('Остаток комплектов', data['totals']['closing'])]:
        summary.append([text_cell(value) for value in row])
    for message in data['errors']:
        summary.append(['Не заполнено', text_cell(message)])
    for message in data['warnings']:
        summary.append(['Предупреждение', text_cell(message)])
    inputs = book.create_sheet('План по каналам')
    inputs.append(['Группа', 'Канал', 'Комплекты', 'DDP стоимость USD', 'Выручка USD', 'Наценка', 'Ввод объёма', 'Состоявшийся факт'])
    for row in data['lines']:
        inputs.append([text_cell(value) for value in [row['group'], row['channel'], row['quantity'], row['cost'], row['revenue'],
                                                     number(row['input']['markup'], optional=True), number(row['input']['input']), number(row['input']['fact'], optional=True)]])
    header = ['Месяц', 'Группа', 'Мощность', 'Канал', 'Модель', 'Ссылка модели', 'Тип блока', 'Количество блоков', 'DDP USD', 'Выручка USD']
    if scenario.state.get('history'):
        from .split_history import presentation
        history = book.create_sheet('Исторический факт')
        history.append(['Факт по дату', scenario.state['history']['through'], 'Количество: Quantity, шт.'])
        history.append(['Группа','Год','Период','Канал','Шт.','Выручка USD','DDP USD','Наценка ×'])
        for group in presentation(scenario)['groups']:
            for year in group['years']:
                for cell in year['annual']:
                    history.append([text_cell(value) for value in [group['name'],year['year'],'Итого',cell['channel'],cell['qty'],cell['revenue'],cell['cost'],cell['markup']]])
                for month in year['months']:
                    for cell in month['cells']:
                        history.append([text_cell(value) for value in [group['name'],year['year'],month['month'],cell['channel'],cell['qty'],cell['revenue'],cell['cost'],cell['markup']]])
    sheet = None
    for index, row in enumerate(data['shipments']):
        if index % 500000 == 0:
            sheet = book.create_sheet('По моделям ' + str(index // 500000 + 1))
            sheet.append(header)
        sheet.append([text_cell(value) for value in [row['month'], row['group'], row['power'], row['channel'], row['model'], row['model_key'], 'Внутренний' if row['kind'] == 'indoor' else 'Наружный', row['quantity'], row['cost'], row['revenue']]])
    stock = book.create_sheet('Остатки')
    stock.append(['Группа', 'Мощность', 'Продажи комплектов', 'Прогноз до начала', 'Дополнительные закупки', 'Внутренние на конец', 'Наружные на конец', 'Комплекты на конец'])
    for row in data['stock']:
        stock.append([text_cell(row[key]) for key in ('group', 'power', 'quantity', 'forecast', 'buy', 'indoor', 'outdoor', 'closing')])
    output = BytesIO()
    try:
        book.save(output)
    finally:
        book.close()
    output.seek(0)
    return FileResponse(output, as_attachment=True, filename=f'split-{scenario.pk}-v{scenario.version}.xlsx')


def dwh_file(scenario):
    if scenario.status != 'approved':
        raise ValidationError('Набор для DWH доступен только для утверждённой версии.')
    data = evaluate(scenario)
    if data['errors']:
        raise ValidationError('Не удалось сформировать детализацию утверждённой версии.')
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=';')
    writer.writerow(['scenario', 'version', 'month', 'channel', 'group_key', 'model_key', 'quantity', 'ddp_usd', 'revenue_usd'])
    for row in data['shipments']:
        writer.writerow([text_cell(value) for value in [str(scenario.family), scenario.version, row['month'], row['channel'], row['group_key'], row['model_key'], row['quantity'], row['cost'], row['revenue']]])
    response = HttpResponse('\ufeff' + buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="split-{scenario.pk}-v{scenario.version}-dwh.csv"'
    return response

