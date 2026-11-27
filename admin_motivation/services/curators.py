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
    data['show_catchup']=any(str(p).strip().casefold()=='год' for p in data.get('periods',[]))
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


@transaction.atomic
def save_global_rates(version_id,goods_id,values,variation_id,revision):
    from ..models import GlobalCoeff,TypeCoeff,SegmentCoeff,VariationCalculate
    version=lock_editable(version_id)
    if str(version.revision)!=str(revision):raise ValidationError('Версия изменилась. Обновите страницу.')
    try:
        goods_id=int(goods_id);variation_id=int(variation_id)
        rates=[Decimal(str(values[key]).replace(',','.'))/100 for key in KEYS]
        if any(not v.is_finite() or not -1<=v<=1 or v!=v.quantize(Decimal('.000001')) for v in rates):raise ValueError
    except (KeyError,InvalidOperation,ValueError,TypeError):raise ValidationError('K0–K4: от −100 до 100%, до четырёх знаков после запятой.')
    good=Goods.objects.filter(pk=goods_id).first()
    variation=VariationCalculate.objects.filter(pk=variation_id).first()
    segments={s.segment_name.upper().replace('O','K',1):s for s in SegmentCoeff.objects.all()}
    if not good or not variation or any(k.upper() not in segments for k in KEYS):raise ValidationError('Выберите товарную матрицу, формулу и проверьте сегменты K0–K4.')
    kind=TypeCoeff.objects.get(type_coeff_name=KIND)
    for key,value in zip(KEYS,rates):
        GlobalCoeff.objects.update_or_create(version=version,goods=good,type_coeff=kind,segment=segments[key.upper()],defaults={'motivation_coeff':float(value),'manager_coeff':1,'variation_calculate':variation})


def global_context(version,selected_goods=None):
    from ..models import GlobalCoeff
    rows={}
    for cell in GlobalCoeff.objects.filter(version=version,type_coeff__type_coeff_name=KIND).select_related('goods','segment','variation_calculate').order_by('goods_id'):
        key=cell.segment.segment_name.upper().replace('O','K',1)
        if key not in ('K0','K1','K2','K3','K4'):continue
        row=rows.setdefault(cell.goods_id,{'goods':cell.goods,'variation':cell.variation_calculate,'variation_id':cell.variation_calculate_id,'values':[None]*5})
        row['values'][int(key[1])]=cell.motivation_coeff
    editing=rows.get(int(selected_goods)) if str(selected_goods).isdigit() else None
    return {'curator_selected_goods':int(selected_goods) if str(selected_goods).isdigit() else (editing['goods'].pk if editing else None),'curator_copy_targets':global_copy_targets(),'global_curator_rows':list(rows.values()),'curator_goods':Goods.objects.order_by('pk'),'curator_editing':editing,
        'curator_inputs':[{'key':key,'value':Decimal(str(editing['values'][i]))*100 if editing and editing['values'][i] is not None else ''} for i,key in enumerate(KEYS)]}


@transaction.atomic
def copy_global_rates(version_id,targets,revision,*,goods_ids=(),replace=False):
    """Copy complete matrices to explicit assigned pairs, preserving other rates."""
    from ..models import GlobalCoeff
    version=lock_editable(version_id)
    if str(version.revision)!=str(revision):raise ValidationError('Версия изменилась. Обновите страницу.')
    if not targets:raise ValidationError('Выберите куратора и подразделение.')
    checked=[];seen=set()
    for employee,subdivision in targets:
        key=(employee.pk,subdivision.pk)
        if key in seen:continue
        if employee.is_archived or not employee.assigned_subdivisions().filter(pk=subdivision.pk).exists():
            raise ValidationError('Куратор должен действовать и быть закреплён за выбранным подразделением.')
        checked.append((employee,subdivision));seen.add(key)
    source=GlobalCoeff.objects.filter(version=version,type_coeff__type_coeff_name=KIND).select_related('segment')
    if goods_ids:source=source.filter(goods_id__in=goods_ids)
    matrices={}
    for cell in source:
        key=cell.segment.segment_name.upper().replace('O','K',1).lower()
        if key not in KEYS:raise ValidationError('Неизвестный сегмент глобальных коэффициентов куратора.')
        matrix=matrices.setdefault(cell.goods_id,{'values':{},'formulas':set()})
        if key in matrix['values']:raise ValidationError('В глобальной матрице повторяется сегмент.')
        value=Decimal(str(cell.motivation_coeff))
        if not value.is_finite() or not -1<=value<=1 or value!=value.quantize(Decimal('.000001')):
            raise ValidationError('В глобальных ставках некорректное значение K0–K4.')
        matrix['values'][key]=value;matrix['formulas'].add(cell.variation_calculate_id)
    if not matrices or goods_ids and set(goods_ids)!=set(matrices):raise ValidationError('Для выбранных товаров нет глобальных коэффициентов кураторов.')
    if any(set(m['values'])!=set(KEYS) or len(m['formulas'])!=1 for m in matrices.values()):
        raise ValidationError('Глобальная матрица должна содержать K0–K4 и одну формулу для каждого товара. Копирование отменено.')
    created=updated=skipped=0
    for employee,subdivision in checked:
        for goods_id,matrix in matrices.items():
            row=CuratorCoefficient.objects.filter(version=version,employee=employee,subdivision=subdivision,goods_id=goods_id).first()
            if row and not replace:skipped+=1;continue
            defaults={**matrix['values'],'variation_calculate_id':next(iter(matrix['formulas']))}
            if row:
                for key,value in defaults.items():setattr(row,key,value)
                row.save();updated+=1
            else:
                CuratorCoefficient.objects.create(version=version,employee=employee,subdivision=subdivision,goods_id=goods_id,**defaults);created+=1
    return {'created':created,'updated':updated,'skipped':skipped}


def global_copy_targets():
    pairs=CuratorEmployee.subdivisions.through.objects.filter(curatoremployee__is_archived=False).order_by('curatoremployee__full_name','subdivision__subdivision_key').values_list('curatoremployee_id','curatoremployee__full_name','subdivision_id','subdivision__subdivision_key')
    return [{'value':f'{employee}:{subdivision}','label':f'{name} · {branch}'} for employee,name,subdivision,branch in pairs]
