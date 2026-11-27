"""Read the portal's one-sheet review exports, including grouped column headers."""
from collections import defaultdict
from django.core.exceptions import ValidationError


def key(value):return ''.join(str(value or '').split()).casefold()


def is_matrix(sheet):
    return sheet.max_row>=4 and key(sheet.cell(2,2).value)=='группаплановпродаж' and key(sheet.cell(4,5).value)=='k0'


def formula_cells(value):
    if value is None or not str(value).strip():return [None]*5
    value=str(value).strip()
    if value.startswith('='):raise ValidationError('Формулы Excel замените текстовым названием формулы расчёта.')
    if value.startswith('K0:') or any(f'K{i}:' in value for i in range(5)):
        result=[None]*5
        for item in value.split(';'):
            label,sep,name=item.strip().partition(':')
            if not sep or label not in ('K0','K1','K2','K3','K4') or not name.strip():raise ValidationError('Неверный список формул по ступеням K0–K4.')
            i=int(label[1])
            if result[i] is not None:raise ValidationError('Повтор формулы ступени.')
            result[i]=name.strip()
        return result
    return [value]*5


def core_sheet(sheet):
    from .core_coeff_import import rate
    rows=list(sheet.iter_rows(values_only=True));blocks=[];scope=[];kind=None
    for column in range(4,sheet.max_column,6):
        if column+5>=sheet.max_column:raise ValidationError('Неполный блок K0–K4 и формулы.')
        value=rows[1][column]
        if value is not None:kind={'политики':'Политики','продажи':'Продажи'}.get(key(value))
        if not kind or [key(v) for v in rows[3][column:column+5]]!=['k0','k1','k2','k3','k4']:raise ValidationError('Не распознаны блоки политик и продаж в выгрузке.')
        names=[v.strip() for v in str(rows[2][column] or '').splitlines() if v.strip()]
        if not names:raise ValidationError('Над блоком ставок отсутствуют подразделения.')
        blocks.append((column,kind,names));scope.append(dict(kind=kind,branches=names))
    result=[]
    for index,values in enumerate(rows[4:],5):
        if not any(v is not None for v in values):continue
        classification=[str(v).strip() if v is not None else '' for v in values[1:4]]
        if not all(classification):raise ValidationError(f'Строка {index}: заполните товарную классификацию.')
        for column,kind,names in blocks:
            raw=values[column:column+5]
            if all(v is None for v in raw):continue
            result.append(dict(sheet=sheet.title,row=index,classification=classification,kind=kind,branches=names,
                rates=[rate(v,precision=12) if v is not None else None for v in raw],formulas=formula_cells(values[column+5]),matrix_export=True))
    return dict(rows=result,scope=scope)


class DirectorBlocks(list):
    def __init__(self,blocks,scoped):super().__init__(blocks);self.scoped=scoped


def director_sheet(sheet):
    from .director_coeff_import import coefficient,label
    rows=list(sheet.iter_rows(values_only=True));blocks=[];scoped=[];last_branch=None
    for column in range(4,sheet.max_column,6):
        if column+5>=sheet.max_column or [key(v) for v in rows[3][column:column+5]]!=['k0','k1','k2','k3','k4']:raise ValidationError('Неверный блок K0–K4 и формулы директора.')
        if rows[1][column] is not None:last_branch=str(rows[1][column])
        visible=[v.strip() for v in str(rows[2][column] or '').splitlines() if v.strip()]
        if not visible or not last_branch:raise ValidationError('Нужны ФИО и подразделение над ставками директора.')
        comment=sheet.cell(3,column+1).comment
        members=[]
        if comment:
            for line in comment.text.splitlines():
                parts=line.split(' · ')
                if len(parts)==3:members.append(dict(branch=parts[0],name=parts[1],code=parts[2]))
        if members:
            branches=list(dict.fromkeys(m['branch'] for m in members))
            expected=[m['name']+(' · '+m['branch'] if len(branches)>1 else '') for m in members]
            if visible!=expected or last_branch!=' / '.join(branches):raise ValidationError('Шапка директоров изменена. Сохраните ФИО и подразделения из выгрузки; изменять можно ставки и формулы.')
        else:
            for name in visible:
                if ' · ' in name:name,branch=name.rsplit(' · ',1)
                elif ' / ' not in last_branch:branch=last_branch
                else:raise ValidationError('Не удалось определить подразделение директора в объединённой шапке.')
                code=comment.text.removeprefix('Код сотрудника: ').strip() if comment and comment.text.startswith('Код сотрудника: ') else None
                members.append(dict(branch=branch,name=name,code=code))
        entries=[]
        for index,values in enumerate(rows[4:],5):
            raw=values[column:column+5]
            if all(v is None for v in raw):continue
            if any(v is None for v in raw):raise ValidationError(f'Строка {index}: заполните все K0–K4 директора; пустые значения не считаются нулём.')
            matrix=tuple(label(v).casefold() for v in values[1:4])
            if len(matrix)!=3 or not all(matrix):raise ValidationError(f'Строка {index}: заполните товарную классификацию.')
            try:rates=tuple(coefficient(v) for v in raw)
            except Exception as exc:raise ValidationError(f'Строка {index}: неверная ставка K0–K4.') from exc
            formula=values[column+5]
            if formula is not None and str(formula).startswith('='):raise ValidationError('В формуле директора должно быть текстовое название, не формула Excel.')
            entries.append((matrix,rates,index));scoped.append(dict(members=members,matrix=matrix,rates=rates,row=index,formula=str(formula).strip() if formula else None))
        blocks.append(([m['name'] for m in members],entries))
    if not scoped:raise ValidationError('В файле нет заполненных ставок директоров.')
    return DirectorBlocks(blocks,scoped)


def apply_directors(blocks,version_id):
    from .directors import lock_editable,KEYS
    from .director_coeff_import import identity
    from .director_formula import resolve,core_formulas
    from .active_directors import bindings,coefficient_rows
    from .subdivision_aliases import coefficient_rows as merged_rows
    from ..models import DirectorEmployee,Subdivision,Goods,SubdivisionDirectorCoeff,VariationCalculate
    from django.db import transaction
    with transaction.atomic():
        version=lock_editable(version_id);active={(v['employee_id'],v['subdivision']) for v in bindings()}
        people=list(DirectorEmployee.objects.filter(is_archived=False));branches=list(Subdivision.all_objects.all())
        goods=defaultdict(list)
        for good in Goods.objects.all():goods[tuple(identity(v) for v in (good.planning_group_sales,good.group,good.brand))].append(good)
        formulas=defaultdict(list)
        for f in VariationCalculate.objects.all():formulas[f.variation_name].append(f)
        original=SubdivisionDirectorCoeff.objects.filter(version=version).select_related('employee','subdivision','goods','variation_calculate')
        effective=merged_rows(((r.employee_id,r.subdivision.subdivision_key,r.goods_id,r) for r in coefficient_rows(original)),1,[0,1,2])
        existing={(eid,branch,gid):row for eid,branch,gid,row in effective};inherited=core_formulas(version)
        prepared={};seen=set();imported=set()
        for entry in blocks.scoped:
            candidates=goods[entry['matrix']]
            if len(candidates)!=1:raise ValidationError(f'Строка {entry["row"]}: товар не найден или неоднозначен.')
            good=candidates[0]
            for member in entry['members']:
                matches=[p for p in people if (p.employee_code==member['code'] if member['code'] else identity(p.full_name)==identity(member['name']))]
                if len(matches)!=1 or identity(matches[0].full_name)!=identity(member['name']):raise ValidationError(f'Директор «{member["name"]}» не найден или неоднозначен.')
                employee=matches[0];branch=member['branch'];key_=(employee.pk,branch,good.pk)
                if (employee.pk,branch) not in active:raise ValidationError(f'{employee.full_name} не закреплён за {branch}.')
                if key_ in seen:raise ValidationError('Повтор товара для директора и подразделения.')
                seen.add(key_);row=existing.get(key_)
                if row is None:
                    targets=[b for b in branches if b.subdivision_key==branch]
                    if len(targets)!=1:raise ValidationError('Не найдено подразделение директора.')
                    row=SubdivisionDirectorCoeff(version=version,employee=employee,subdivision=targets[0],goods=good)
                formula=entry['formula']
                if formula and (row.pk is None or formula!=resolve(version,good,branch,row,inherited=inherited)[0]):
                    options=formulas[formula]
                    if not options:
                        from .planning_group_rules import FORMULAS
                        if formula in FORMULAS:options=[VariationCalculate.objects.get_or_create(variation_name=formula)[0]];formulas[formula]=options
                    if len(options)!=1:raise ValidationError(f'Не найдена формула «{formula}».')
                    row.variation_calculate=options[0]
                for name,value in zip(KEYS,entry['rates']):setattr(row,name,value)
                prepared[key_]=row;imported.add(employee.pk)
        created=[];updated=[];unchanged=0
        stored={r.pk:r for r in SubdivisionDirectorCoeff.objects.filter(pk__in=[r.pk for r in prepared.values() if r.pk])}
        for row in prepared.values():
            old=stored.get(row.pk)
            if old and all(getattr(old,k)==getattr(row,k) for k in (*KEYS,'variation_calculate_id')):unchanged+=1
            elif old:updated.append(row)
            else:created.append(row)
        SubdivisionDirectorCoeff.objects.bulk_create(created,batch_size=100)
        SubdivisionDirectorCoeff.objects.bulk_update(updated,[*KEYS,'variation_calculate'],batch_size=100)
        return dict(directors=len(imported),created=len(created),updated=len(updated),unchanged=unchanged)
