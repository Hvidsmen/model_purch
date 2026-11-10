"""Two weighted premium/plan coefficients over a selected full 2026 plan."""
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from io import BytesIO
from django.core.exceptions import ValidationError
from django.db import transaction
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from ..models import SalesPlanScenario, GlobalCoeffVersion, Subdivision, IntegralPlan2026Calculation
from .comparison import Index, coefficient_rows
from .director_comparison import DirectorIndex
from .active_directors import bindings
from .subdivision_aliases import mapping, canonical
from .subdivision_scope import included
from .heat_exclusion import is_heat
from .sales_plans import number, SEGMENTS

ZERO = Decimal(0)
RULE = 'integral-plan-2026-v2-known-subdivisions'


def empty(branch):
    return {'branch': branch, 'plan': ZERO, 'policies': ZERO, 'sales': ZERO,
            'directors': ZERO, 'director_count': 0, 'notes': set(), 'staff': {}}


def add(left, right):
    return None if left is None or right is None else left + right


def finish(row):
    combined = add(row['policies'], row['sales'])
    plan = row['plan']
    row['combined'] = combined
    row['combined_coefficient'] = combined / plan if plan > 0 and combined is not None else None
    row['directors_coefficient'] = row['directors'] / plan if plan > 0 and row['directors'] is not None else None
    for key in ('combined','directors'):
        value = row[key + '_coefficient']
        row[key + '_percent'] = value * 100 if value is not None else None
    if plan <= 0:
        row['notes'].add('План нулевой или отрицательный: интегральный коэффициент не определён')
    row['notes'] = sorted(row['notes'])
    row['staff'] = sorted(row['staff'].values(), key=lambda r:(r['name'],r['id']))
    return row


@transaction.atomic
def calculate(scenario_id, version_id):
    scenario = SalesPlanScenario.objects.select_for_update().get(pk=scenario_id, is_deleted=False)
    version = GlobalCoeffVersion.objects.select_for_update().get(pk=version_id)
    if version.effective_from >= date(2027,1,1):
        raise ValidationError('Выбранная версия коэффициентов начинает действовать после 2026 года.')
    if version.status != 'approved':
        raise ValidationError('Для расчёта выберите утверждённую версию коэффициентов.')
    if scenario.loaded_at is None:
        raise ValidationError('Сначала загрузите выбранный план продаж.')
    aliases = mapping()
    directory = {row.subdivision_key: row for row in Subdivision.all_objects.select_related('chanel')}
    roster = defaultdict(dict)
    for row in bindings():
        roster[row['subdivision']][row['employee_id']] = row['full_name']
    policy_index = Index(coefficient_rows([version.pk], subdivision=True))
    director_index = DirectorIndex([version.pk], local=True)
    grouped, months, input_count = {}, set(), 0
    skipped = {}
    lines = scenario.lines.filter(plan_date__gte=date(2026,1,1),plan_date__lt=date(2027,1,1)).exclude(subdivision='')
    for day, sub, pg, goods, brand, amount, segments in lines.order_by().values_list(
            'plan_date','subdivision','planning_group_sales','group','brand','amount_usd','segment_amounts').iterator(chunk_size=2000):
        if not included(sub, aliases):
            continue
        target = canonical(sub, aliases)
        branch = directory.get(sub) or directory.get(target)
        if branch is None:
            excluded = skipped.setdefault(sub, {'branch':sub,'plan':ZERO,'lines':0})
            excluded['plan'] += number(amount); excluded['lines'] += 1
            continue
        months.add(day.strftime('%Y-%m')); input_count += 1
        dpp = branch.chanel.chanel_name.strip().casefold() == 'дпп'
        if not isinstance(segments,dict) or set(SEGMENTS) - set(segments):
            raise ValidationError(f'{sub}, {pg}: в плане отсутствует распределение O0–O4. Перезагрузите план.')
        values = [number(segments[key]) for key in SEGMENTS]
        plan = number(amount)
        if abs(sum(values,ZERO)-plan) > max(Decimal('.0001'),abs(plan)*Decimal('.00000001')):
            raise ValidationError(f'{sub}, {pg}: сумма O0–O4 не совпадает с планом.')
        entry = grouped.setdefault((target,dpp,pg,goods,brand),[ZERO,[ZERO]*5])
        entry[0] += plan
        entry[1] = [a+b for a,b in zip(entry[1],values)]
    if not grouped and not skipped:
        raise ValidationError('В выбранном загруженном плане нет строк подразделений за 2026 год.')
    buckets, details = {}, []
    for (sub,dpp,pg,goods,brand),(plan,amounts) in sorted(grouped.items()):
        row = buckets.setdefault(sub,empty(sub));row['plan'] += plan
        local = {'branch':sub,'planning':pg,'goods':goods,'brand':brand,'plan':plan,
                 'segments':amounts,'policies':ZERO,'sales':ZERO,'directors':ZERO,'notes':[]}
        for kind,field in [('Политики','policies'),('Продажи','sales')]:
            if kind == 'Политики' and (is_heat(pg) or dpp):
                local['notes'].append('Политики: исключён HEAT' if is_heat(pg) else 'Политики: исключён канал ДПП')
                continue
            try:
                matrix,fallback = policy_index.coefficients(version.pk,sub,pg,goods,brand,kinds=[kind])
                value = sum((k*usd for k,usd in zip(matrix[0],amounts)),ZERO)
                local[field] = value
                if fallback:
                    note = f'{kind}: использован минимум вместо точной настройки'
                    row['notes'].add(note);local['notes'].append(note)
            except ValidationError:
                local[field] = None
                row['notes'].add(f'{kind}: нет коэффициентов, результат не определён')
            row[field] = add(row[field],local[field])
        staff = roster.get(sub,{})
        row['director_count'] = len(staff)
        if not staff:
            row['notes'].add('Нет действующих привязанных директоров')
        for employee_id,name in sorted(staff.items()):
            rates,source = director_index.coefficients(version.pk,sub,employee_id,pg,goods,brand)
            value = sum((k*usd for k,usd in zip(rates,amounts)),ZERO) if source != 'missing' else None
            if source != 'exact':
                note = ('Нет коэффициентов директора: ' if source == 'missing' else 'Минимальные коэффициенты директора: ') + name
                row['notes'].add(note);local['notes'].append(note)
            person = row['staff'].setdefault(employee_id,{'id':employee_id,'name':name,'premium':ZERO})
            person['premium'] = add(person['premium'],value)
            local['directors'] = add(local['directors'],value)
        row['directors'] = add(row['directors'],local['directors'])
        details.append(local)
    company = empty('Итого по учтённым подразделениям')
    if skipped:
        company['notes'].add('Подразделения вне справочника исключены из плана и премий: ' + ', '.join(sorted(skipped)))
    for row in buckets.values():
        for field in ('plan','policies','sales','directors'):
            company[field] = add(company[field],row[field])
        company['notes'].update(row['notes'])
    company['director_count'] = len({i for sub in buckets for i in roster.get(sub,{})})
    rows = [finish(row) for _,row in sorted(buckets.items())]
    total = finish(company)
    metadata = {'rule':RULE,'year':2026,'months':sorted(months),'input_count':input_count,'scenario':scenario.title,'source_version':scenario.source_version,
        'loaded_at':scenario.loaded_at.isoformat(),'coefficient_version':version.title or f'Версия № {version.pk}',
        'coefficient_effective_from':version.effective_from.isoformat(),'coefficient_revision':version.revision,
        'coefficient_version_id':version.pk,'subdivision_mapping':aliases,
        'skipped_subdivisions':[skipped[key] for key in sorted(skipped)],
        'skipped_plan':sum((item['plan'] for item in skipped.values()),ZERO),
        'scope':'Только подразделения из справочника портала; действующие объединения',
        'period':'Полный 2026 год, без ограничения IV кварталом',
        'formula':'Политики + Продажи: (премия политиков + премия продаж) / план; Директора: сумма премий действующих директоров / план'}
    payload = json.loads(json.dumps({'metadata':metadata,'rows':rows,'total':total,'details':details},ensure_ascii=False,default=str))
    return IntegralPlan2026Calculation.objects.create(scenario=scenario,coefficient_version=version,results=payload)


def workbook(snapshot):
    data=snapshot.results;book=Workbook();book.remove(book.active)
    sheet=book.create_sheet('Интегральные 2026')
    sheet.append(['План 2026 · '+data['metadata']['scenario']])
    sheet.append(['Коэффициенты: '+data['metadata']['coefficient_version']+' · '+data['metadata']['period']])
    sheet.append(['Подразделение','План, USD','Премия Политики + Продажи, USD','Коэффициент Политики + Продажи',
                  'Премия директоров, USD','Коэффициент Директора','Действующих директоров','Примечание'])
    for row in [*data['rows'],data['total']]:
        sheet.append([row['branch'],numeric(row['plan']),numeric(row['combined']),numeric(row['combined_coefficient']),
                      numeric(row['directors']),numeric(row['directors_coefficient']),row['director_count'],'; '.join(row['notes'])])
    style(sheet,3,{4,6},{2,3,5},'B4')
    sheet=book.create_sheet('Директора')
    sheet.append(['Расшифровка премии действующих директоров · USD'])
    sheet.append(['Подразделение','Директор','Расчётная премия, USD'])
    for row in data['rows']:
        for person in row['staff']:
            sheet.append([row['branch'],person['name'],numeric(person['premium'])])
    style(sheet,2,set(),{3},'C3')
    sheet=book.create_sheet('Основание')
    sheet.append(['Плановые суммы по сегментам скидки и премии · USD'])
    sheet.append(['Подразделение','Группа планов продаж','Группа ERP','Бренд','План, USD',
                  *[f'План {s}, USD' for s in SEGMENTS],'Политики, USD','Продажи, USD','Директора, USD','Примечание'])
    for row in data['details']:
        sheet.append([row['branch'],row['planning'],row['goods'],row['brand'],numeric(row['plan']),
            *map(numeric,row['segments']),numeric(row['policies']),numeric(row['sales']),numeric(row['directors']),'; '.join(row['notes'])])
    style(sheet,2,set(),set(range(5,14)),'E3')
    skipped=data['metadata'].get('skipped_subdivisions',[])
    if skipped:
        sheet=book.create_sheet('Исключённые подразделения')
        sheet.append(['Подразделения отсутствуют в справочнике: не включены в план и премии'])
        sheet.append(['Подразделение','Исключённый план, USD','Строк плана'])
        for item in skipped:sheet.append([item['branch'],numeric(item['plan']),item['lines']])
        style(sheet,2,set(),{2},'B3')
    output=BytesIO();book.save(output);output.seek(0);return output


def numeric(value):
    return None if value is None else Decimal(str(value))


def style(sheet,header,percents,money,freeze):
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value,str):cell.data_type='s'
            if cell.row==header:
                cell.font=Font(bold=True,color='243B53');cell.fill=PatternFill('solid',fgColor='EDE9FE')
            elif cell.row>header:
                if cell.column in percents:cell.number_format='0.0000%'
                elif cell.column in money:cell.number_format='#,##0.00 "USD"'
    for col in range(1,sheet.max_column+1):sheet.column_dimensions[get_column_letter(col)].width=30
    sheet.freeze_panes=freeze
    sheet.auto_filter.ref=f'A{header}:{get_column_letter(sheet.max_column)}{max(header,sheet.max_row)}'
