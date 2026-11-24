"""Curator assignments and versioned rates. No director records are reused."""
from decimal import Decimal, InvalidOperation
from io import BytesIO
from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import CuratorEmployee,CuratorCoefficient,Goods,GlobalCoeffVersion
from .directors import lock_editable

KIND='Кураторы'
KEYS=tuple(f'k{i}' for i in range(5))


def snapshot():
    return {
        'employees':list(CuratorEmployee.objects.order_by('pk').values('id','full_name','employee_code','user_id','is_archived')),
        'assignments':list(CuratorEmployee.subdivisions.through.objects.order_by('pk').values()),
        'coefficients':[{key:str(value) if isinstance(value,Decimal) else value for key,value in row.items()} for row in CuratorCoefficient.objects.order_by('pk').values()],
    }


@transaction.atomic
def save_rates(version_id,employee,subdivision,rows,revision):
    version=lock_editable(version_id)
    if str(version.revision)!=str(revision):raise ValidationError('Версия изменилась. Обновите страницу.')
    if employee.is_archived or not employee.assigned_subdivisions().filter(pk=subdivision.pk).exists():
        raise ValidationError('Куратор должен быть закреплён за выбранным подразделением.')
    checked=[];seen=set()
    for row in rows:
        try:
            good=int(row['goods']);values=[Decimal(str(row[key]))/100 for key in KEYS]
            if Decimal(str(row['goods']))!=good or good in seen or not Goods.objects.filter(pk=good).exists() or any(not v.is_finite() or not -1<=v<=1 or v!=v.quantize(Decimal('.000001')) for v in values):raise ValueError()
        except (KeyError,ValueError,TypeError,InvalidOperation):raise ValidationError('Проверьте товарную матрицу и K0–K4: от −100 до 100%, до четырёх знаков после запятой. Повторы товаров недопустимы.')
        seen.add(good);checked.append((good,values))
    if not checked:raise ValidationError('В файле нет коэффициентов.')
    for good,values in checked:
        record,_=CuratorCoefficient.objects.get_or_create(version=version,employee=employee,subdivision=subdivision,goods_id=good)
        for key,value in zip(KEYS,values):setattr(record,key,value)
        record.save()
    return len(checked)


def workbook(employee,subdivision,version):
    from openpyxl import Workbook
    book=Workbook();sheet=book.active;sheet.title='Кураторы'
    sheet.append(['GoodsId','Товарная матрица',*[key.upper()+', %' for key in KEYS]])
    existing={r.goods_id:r for r in CuratorCoefficient.objects.filter(version=version,employee=employee,subdivision=subdivision)}
    for good in Goods.objects.order_by('pk'):
        rate=existing.get(good.pk)
        sheet.append([good.pk,str(good),*[getattr(rate,key)*100 if rate else None for key in KEYS]])
        sheet.cell(sheet.max_row,2).data_type='s'
    sheet.freeze_panes='C2';sheet.auto_filter.ref=sheet.dimensions
    info=book.create_sheet('Параметры');info.append(['Куратор',employee.full_name]);info.append(['Подразделение',subdivision.subdivision_key]);info.append(['Версия',str(version)])
    for row in info:
        for cell in row:cell.data_type='s'
    output=BytesIO();book.save(output);book.close();output.seek(0);return output


def read_upload(upload):
    from openpyxl import load_workbook
    if upload.size>10*1024*1024:raise ValidationError('Файл больше 10 МБ.')
    try:
        book=load_workbook(upload,read_only=True,data_only=False)
        try:
            sheet=book['Кураторы'];rows=sheet.iter_rows(values_only=True)
            header=next(rows,None)
            if not header or tuple(header[:7])!=('GoodsId','Товарная матрица',*[key.upper()+', %' for key in KEYS]):raise ValueError()
            result=[]
            for line in rows:
                if all(value is None for value in line[2:7]):continue
                if len(result)>=50000 or len(line)<7 or any(isinstance(v,str) and v.startswith('=') for v in line):raise ValueError()
                result.append({'goods':line[0],**dict(zip(KEYS,line[2:7]))})
            return result
        finally:book.close()
    except ValidationError:raise
    except Exception:raise ValidationError('Используйте шаблон «Кураторы» без формул. Заполните все пять ставок в строке.')


def finish_report(data):
    rows=data.get('_source_rows',[])
    if rows and {r.get('ТипКоэф') for r in rows}=={KIND} and len({r.get('Подразделение') for r in rows})>1:
        data['curator_multiple']=True
        for blocks in (data.get('detail_totals',[]),data.get('division_execution',[])):
            for block in blocks:
                for index in (10,11,12,14,15,16):block[index]=None
    return data


def personal_sections(sections):
    from .manager_detail import manager_detail_report
    result=[]
    for section in sections:
        data=section['data']
        if section['kind']==KIND and data.get('curator_multiple'):
            for branch in sorted({r['Подразделение'] for r in data['_source_rows']}):
                source=[r for r in data['_source_rows'] if r['Подразделение']==branch]
                performance=[r for r in data.get('_performance_rows') or [] if r['Подразделение']==branch]
                result.append({'kind':KIND,'branch':branch,'data':manager_detail_report(source,data['currency'],performance_rows=performance)})
        else:result.append(section)
    return result


def enabled():
    from django.db.models import F
    return CuratorCoefficient.objects.filter(version__status='approved',employee__is_archived=False,employee__subdivisions=F('subdivision')).exists()
