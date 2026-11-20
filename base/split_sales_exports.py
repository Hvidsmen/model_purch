"""Monthly sales workbooks from the approved snapshot and engine allocations."""
from collections import OrderedDict
from io import BytesIO

from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from .split_engine import evaluate, planning_months, precise, ZERO
from .split_exports import text_cell

VARIANTS = {'groups':'По группам', 'powers':'По мощностям', 'models':'По моделям'}
REFERENCE_HEADERS = ['Группа планирования','Канал','Группа планов продаж','Бренд / марка',
                     'Тип наружного блока','Производитель / завод','Код группы']


def reference(group, channel):
    ref = group['ref']
    details = ref.get('details') or {}
    # Approved exports must not consult the current directory for missing fields.
    return [ref['name'],channel,details.get('sales_plan_group') or '',
            details.get('brand') or ', '.join(ref.get('brand',[])),
            details.get('outdoor_type') or '',details.get('factory') or '',ref['source_key']]


@precise
def rows(scenario, variant, data):
    months = planning_months(scenario)
    result = OrderedDict()
    group_keys = {}
    def ensure(gid,cid,power='',kind='',model_key='',model=''):
        key = (gid,cid) if variant == 'groups' else (gid,cid,power) if variant == 'powers' else (gid,cid,power,kind,model_key)
        if key not in result:
            fields = reference(scenario.state['groups'][gid],scenario.state['channels'][cid]['name'])
            if variant != 'groups': fields.append(power)
            if variant == 'models': fields.extend([model or 'Не задана',model_key,'Внутренний' if kind=='indoor' else 'Наружный'])
            result[key] = {'fields':fields,'kind':kind,'months':{month:{metric:ZERO for metric in ('quantity','cost','revenue')} for month in months}}
        return result[key]
    for gid,group in scenario.state['groups'].items():
        source_key = group['ref']['source_key']
        if source_key in group_keys: raise ValidationError('В сценарии повторяется код группы: '+source_key+'. Проверьте состав.')
        group_keys[source_key] = gid
        for cid in group['channels']:
            if variant == 'groups': ensure(gid,cid);continue
            powers = [(key,power) for key,power in group['powers'].items() if power.get('active')]
            if not powers: powers = [('',{'models':[]})]
            for power_key,power in powers:
                if variant == 'powers': ensure(gid,cid,power_key);continue
                models = {str(model['id']):model for model in power['models']}
                for kind in ('indoor','outdoor'):
                    model = models.get(str(power.get(kind)),{})
                    if model.get('kind') != kind: model = {}
                    ensure(gid,cid,power_key,kind,model.get('key',''),model.get('name',''))
    for shipment in data['shipments']:
        row = ensure(group_keys[shipment['group_key']],shipment['channel_id'],shipment['power'],
                     shipment['kind'],shipment['model_key'],shipment['model'])
        cell = row['months'][shipment['month']]
        # Both blocks form one set; money is split between blocks and adds up.
        if variant == 'models' or shipment['kind'] == 'indoor': cell['quantity'] += shipment['quantity']
        cell['cost'] += shipment['cost']
        cell['revenue'] += shipment['revenue']
    return list(result.values())


def styled(sheet, values, *, heading=False, total=False, numeric_from=None):
    cells = []
    for index,value in enumerate(values):
        cell = WriteOnlyCell(sheet,value=text_cell(value))
        if heading or total:
            cell.font = Font(bold=True,color='FFFFFF' if heading else '283451')
            cell.fill = PatternFill('solid',fgColor='6550B9' if heading else 'EEE9FF')
        if heading: cell.alignment = Alignment(wrap_text=True,vertical='center')
        if numeric_from is not None and index >= numeric_from: cell.number_format = '#,##0.######;[Red]-#,##0.######;0'
        cells.append(cell)
    sheet.append(cells)


@precise
def workbook(scenario, variant):
    if variant not in VARIANTS: raise ValidationError('Выберите вариант выгрузки плана продаж.')
    if scenario.status != 'approved': raise ValidationError('Три варианта плана продаж доступны после утверждения сценария.')
    data = evaluate(scenario)
    if data['errors']: raise ValidationError('Не удалось выгрузить утверждённый план: '+ '; '.join(data['errors']))
    months = planning_months(scenario)
    entries = rows(scenario,variant,data)
    headers = list(REFERENCE_HEADERS)
    if variant != 'groups': headers.append('Мощность')
    if variant == 'models': headers.extend(['Модель','Код модели','Тип блока'])
    base_count = len(headers)
    headers.extend([*months,'Итого за период'])
    book = Workbook(write_only=True)
    try:
        info = book.create_sheet('Сценарий')
        for values in [('Сценарий',scenario.title),('Версия',scenario.version),('Ревизия',scenario.revision),
                       ('Статус','Утверждён'),('Утверждён',timezone.localtime(scenario.approved_at).strftime('%d.%m.%Y %H:%M') if scenario.approved_at else ''),
                       ('Начало периода',str(scenario.start)),('Конец периода',str(scenario.end)),
                       ('Детализация',VARIANTS[variant]),('Количество','Штуки блоков' if variant == 'models' else 'Комплекты'),
                       ('Общий план продаж, компл.',data['totals']['quantity']),
                       ('Выручка всего, USD',data['totals']['revenue']),('DDP всего, USD',data['totals']['cost']),
                       ('Область','Весь утверждённый сценарий, все группы и каналы; архив исключён'),
                       ('Основание','Сохранённый снимок утверждённого сценария. Количества и суммы распределены по правилам расчёта плана.'),
                       ('Справочные поля','Из снимка сценария. Пустое поле означает, что оно не сохранено; текущий справочник не подставляется.'),
                       ('Единицы','Внутренние и наружные блоки показаны отдельно; сумма штук обоих типов не является количеством комплектов.')]:
            info.append([text_cell(value) for value in values])
        for warning in data['warnings']: info.append(['Предупреждение',text_cell(warning)])
        # Split sheets before Excel's row limit; each part has its own subtotal.
        for metric,title in [('quantity','Количество'),('revenue','Выручка USD'),('cost','DDP USD')]:
            batches = [entries[start:start+500000] for start in range(0,len(entries),500000)] or [[]]
            for part,batch in enumerate(batches,1):
                sheet = book.create_sheet(title if len(batches)==1 else title+' '+str(part))
                sheet.freeze_panes = 'C2'
                for index in range(1,len(headers)+1):
                    sheet.column_dimensions[get_column_letter(index)].width = 17 if index>base_count else 40 if index==1 else 22
                styled(sheet,headers,heading=True)
                totals = [ZERO for month in months]
                kinds = {kind:[ZERO for month in months] for kind in ('indoor','outdoor')}
                for entry in batch:
                    amounts = [entry['months'][month][metric] for month in months]
                    styled(sheet,[*entry['fields'],*amounts,sum(amounts,ZERO)],numeric_from=base_count)
                    totals = [left+right for left,right in zip(totals,amounts)]
                    if variant == 'models': kinds[entry['kind']] = [left+right for left,right in zip(kinds[entry['kind']],amounts)]
                # Filter excludes footers, so filtering never hides an ordinary data row.
                sheet.auto_filter.ref = 'A1:'+get_column_letter(len(headers))+str(len(batch)+1)
                unit = 'шт. блоков' if variant == 'models' and metric == 'quantity' else 'компл.' if metric == 'quantity' else 'USD'
                styled(sheet,['Итого · '+unit,*['']*(base_count-1),*totals,sum(totals,ZERO)],total=True,numeric_from=base_count)
                if variant == 'models' and metric == 'quantity':
                    for kind,label in [('indoor','Внутренние · шт.'),('outdoor','Наружные · шт.')]:
                        values = kinds[kind]
                        styled(sheet,[label,*['']*(base_count-1),*values,sum(values,ZERO)],total=True,numeric_from=base_count)
        output = BytesIO()
        book.save(output)
    finally: book.close()
    output.seek(0)
    return FileResponse(output,as_attachment=True,filename=f'sales-plan-{scenario.pk}-v{scenario.version}-{variant}.xlsx')
