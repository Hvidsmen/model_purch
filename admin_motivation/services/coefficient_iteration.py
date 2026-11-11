"""Parse uploaded scenario inputs and suggest exact, reversible mappings only."""
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile
from django.core.exceptions import ValidationError
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from ..models import Subdivision,DirectorEmployee


def norm(value):return ' '.join(str(value or '').split()).casefold().replace('ё','е')
def label(value):return ' '.join(str(value or '').split())


def read_file(upload,*,directors=False):
    data=upload.read(10*1024*1024+1)
    if len(data)>10*1024*1024:raise ValidationError('Файл превышает 10 МБ.')
    book=None
    try:
        with ZipFile(BytesIO(data)) as archive:
            if len(archive.infolist())>1000 or sum(i.file_size for i in archive.infolist())>50*1024*1024:
                raise ValidationError('Слишком большой распакованный Excel.')
        book=load_workbook(BytesIO(data),read_only=True,data_only=False)
        sheet=book.worksheets[0]
        if sheet.max_row>5000 or sheet.max_column>128:raise ValidationError('Слишком большая таблица коэффициентов.')
        rows=list(sheet.values)
        header=next((i for i,row in enumerate(rows) if any(norm(v)=='группа планов продаж' for v in row)
                     and any(norm(v)=='марка / бренд' for v in row)),None)
        if header is None:raise ValidationError('Не найдены заголовки товарной матрицы.')
        segments=header+1
        if segments>=len(rows):raise ValidationError('Нет заголовков K0–K4.')
        blocks=[];warnings=[]
        for column in range(len(rows[segments])-4):
            if [label(v) for v in rows[segments][column:column+5]]!=[f'K{i}' for i in range(5)]:continue
            kind='Директора' if directors else label(rows[header][column])
            if kind not in (('Директора',) if directors else ('Политики','Продажи')):raise ValidationError('Не распознан вид блока коэффициентов.')
            raw_names=[label(value) for row in rows[:header] for value in row[column:column+5] if isinstance(value,str) and label(value)]
            names=list(dict.fromkeys(raw_names))
            if len(raw_names)!=len(names):warnings.append(f'{sheet.title}: повторы имён в блоке {column+1} объединены.')
            entries=[];keys=set()
            for number,row in enumerate(rows[segments+1:],segments+2):
                if not row or not (isinstance(row[0],(int,float)) or str(row[0] or '').isdigit()):continue
                key=tuple(label(value) for value in row[1:4])
                if len(key)!=3 or not key[0] or not key[1]:raise ValidationError(f'Строка {number}: не заполнены товарные ключи.')
                identity=tuple(norm(value) for value in key)
                if identity in keys:raise ValidationError(f'Строка {number}: повтор товарного сочетания в блоке.')
                keys.add(identity)
                try:
                    values=[Decimal(str(v)) for v in row[column:column+5]]
                    if len(values)!=5 or any(not v.is_finite() or abs(v)>1 for v in values):raise ValueError
                except (InvalidOperation,ValueError,TypeError):
                    raise ValidationError(f'Строка {number}, блок {column+1}: нужны пять числовых K0–K4 от -1 до 1, без формул.')
                entries.append({'key':list(key),'rates':[str(v) for v in values],'row':number})
            if not names or not entries:raise ValidationError(f'Блок {column+1}: нет получателей или ставок.')
            blocks.append({'kind':kind,'column':column+1,'labels':names,'entries':entries})
        if not blocks:raise ValidationError('Не найдено блоков K0–K4.')
        if len(blocks)>20 or sum(len(b['labels']) for b in blocks)>200:raise ValidationError('Слишком много блоков или получателей.')
        return {'name':upload.name[:255],'sha256':sha256(data).hexdigest(),'blocks':blocks,'warnings':warnings}
    except ValidationError:raise
    except Exception as exc:
        raise ValidationError('Не удалось прочитать файл .xlsx с коэффициентами.') from exc
    finally:
        if book:book.close()


def mapping_rows(payload):
    result=[]
    for source in ('branches','directors'):
        for block_index,block in enumerate(payload[source]['blocks']):
            for index,name in enumerate(block['labels']):
                result.append({'id':f'{source}_{block_index}_{index}','source':source,'label':name,
                    'kind':block['kind'],'block':block_index,'range':f"{get_column_letter(block['column'])}:{get_column_letter(block['column']+4)}",'entries':len(block['entries'])})
    return result


def suggestions(payload):
    branches=list(Subdivision.all_objects.all());people=list(DirectorEmployee.objects.filter(is_archived=False).prefetch_related('subdivisions'))
    by_key=defaultdict(list);by_name=defaultdict(list);by_person=defaultdict(list)
    for sub in branches:by_key[norm(sub.subdivision_key)].append(sub.pk);by_name[norm(sub.subdivision_name)].append(sub.pk)
    for person in people:by_person[norm(person.full_name)].append(person)
    choices={}
    for row in mapping_rows(payload):
        if row['source']=='branches':
            matches=by_key.get(norm(row['label'])) or by_name.get(norm(row['label']),[])
            choices[row['id']]={'subdivision':matches[0] if len(matches)==1 else None}
        else:
            matches=by_person.get(norm(row['label']),[])
            person=matches[0] if len(matches)==1 else None
            choices[row['id']]={'employee':person.pk if person else None,'subdivisions':list(person.subdivisions.values_list('pk',flat=True)) if person else []}
    return choices


def check_conflicts(payload,mappings):
    assigned={}
    for row in mapping_rows(payload):
        value=mappings.get(row['id'],{});block=payload[row['source']]['blocks'][row['block']]
        targets=[('branch',value.get('subdivision'),row['kind'])] if row['source']=='branches' else [
            ('director',sub,value.get('employee')) for sub in value.get('subdivisions',[])]
        for target in targets:
            if target[1] is None or target[2] is None:continue
            for entry in block['entries']:
                key=(*target,*(norm(value) for value in entry['key']));rates=tuple(Decimal(v) for v in entry['rates'])
                if key in assigned and assigned[key]!=rates:
                    raise ValidationError(f"Конфликт ставок после сопоставления: {row['label']} · {' / '.join(entry['key'])}. Разведите получателей по подразделениям.")
                assigned[key]=rates
