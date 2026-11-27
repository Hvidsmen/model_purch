"""Previewed policies/sales matrices grouped by branch lists above K0–K4."""
from decimal import Decimal,InvalidOperation
from io import BytesIO
import re,zipfile
from openpyxl import load_workbook
from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import Goods,Subdivision,TypeCoeff,SegmentCoeff,SubdivisionCoeff,VariationCalculate
from .directors import lock_editable


def norm(value):return re.sub(r'\s+','',str(value or '')).casefold()


def rate(value,*,precision=6):
    if value is None or isinstance(value,bool):raise ValidationError('Заполните каждую K0–K4: пустое значение не считается нулём.')
    try:
        text=str(value).replace(' ','').replace(',','.')
        value=Decimal(text.rstrip('%'))/(100 if text.endswith('%') else 1)
        rounded=value.quantize(Decimal(1).scaleb(-precision))
        if not value.is_finite() or not -1<=value<=1 or abs(value-rounded)>Decimal('0.000000000001'):raise ValueError
        return str(rounded)
    except (ValueError,InvalidOperation):raise ValidationError('Ставка должна быть от −100% до 100%, до четырёх знаков в процентах.')


def parse(upload, *, with_scope=False):
    content=upload.read(10*1024*1024+1)
    if len(content)>10*1024*1024:raise ValidationError('Файл Excel должен быть не больше 10 МБ.')
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist())>100*1024*1024:raise ValidationError('Распакованный Excel слишком большой.')
        book=load_workbook(BytesIO(content),read_only=True,data_only=False,keep_links=False)
    except ValidationError:raise
    except Exception as exc:raise ValidationError('Не удалось открыть XLSX-файл.') from exc
    result=[];found=False;scope=[]
    try:
        for sheet in book:
            from .export_matrix_import import is_matrix,core_sheet
            if sheet.title=='Подразделения' and is_matrix(sheet):
                if sheet.max_row>5000 or sheet.max_column>16384 or sheet.max_row*sheet.max_column>2000000:raise ValidationError('Выгрузка слишком большая. Разделите файл.')
                parsed=core_sheet(sheet);result.extend(parsed['rows']);scope.extend(parsed['scope']);found=True;continue
            if sheet.max_row>2000 or sheet.max_column>256:raise ValidationError('В шаблоне допускается до 2000 строк и 256 колонок.')
            rows=list(sheet.iter_rows(values_only=True));header=None
            for i,row in enumerate(rows):
                if len(row)>3 and norm(row[1])=='группаплановпродаж' and norm(row[2])=='группаerp':header=i;break
            if header is None:continue
            found=True
            if header==0:raise ValidationError('В этом файле нет списков подразделений над ставками: это глобальный шаблон. Для сверки выберите файл ставок по подразделениям.')
            if header+1>=len(rows):raise ValidationError('Не найдена строка K0–K4.')
            blocks=[];assigned=set()
            for start,value in enumerate(rows[header]):
                kind={'политики':'Политики','продажи':'Продажи'}.get(norm(value))
                if not kind:continue
                steps=[norm(v).replace('к','k') for v in rows[header+1][start:start+5]]
                if steps!=['k0','k1','k2','k3','k4']:raise ValidationError('В каждой группе должны идти K0, K1, K2, K3, K4.')
                branches=[]
                for previous in rows[:header]:
                    for value in previous[start:start+5]:
                        if value is None:continue
                        if not isinstance(value,str) or value.startswith('='):raise ValidationError('Над блоками должны быть текстовые списки подразделений.')
                        for name in value.splitlines():
                            name=name.strip()
                            if name and name not in branches:branches.append(name)
                if not branches:raise ValidationError(f'Над блоком «{kind}» нет подразделений.')
                for branch in branches:
                    key=(kind,norm(branch))
                    if key in assigned:raise ValidationError(f'Подразделение «{branch}» повторяется в блоках «{kind}».')
                    assigned.add(key)
                blocks.append((start,kind,branches))
                scope.append({'kind':kind,'branches':branches})
            if not blocks:raise ValidationError('Не найдены блоки «Политики» / «Продажи».')
            for index,row in enumerate(rows[header+2:],header+3):
                if not any(v is not None for v in row):continue
                classification=[str(v).strip() if v is not None else '' for v in row[1:4]]
                if len(classification)!=3 or not all(classification):raise ValidationError(f'Строка {index}: заполните группу планов, ERP и бренд; используйте * для общей матрицы.')
                for start,kind,branches in blocks:
                    values=row[start:start+5]
                    if all(v is None for v in values):continue
                    if any(isinstance(v,str) and v.startswith('=') for v in values):raise ValidationError(f'Строка {index}: замените формулы коэффициентов их значениями.')
                    result.append({'sheet':sheet.title,'row':index,'classification':classification,'kind':kind,'branches':branches,'rates':[rate(v) for v in values]})
            if len(result)>12000:raise ValidationError('Слишком много матриц. Разделите файл.')
        if not found or not result and not with_scope:raise ValidationError('В файле нет заполненных коэффициентов указанного шаблона.')
        return {'rows':result,'scope':scope} if with_scope else result
    except ValidationError:raise
    except Exception as exc:raise ValidationError('Не удалось прочитать ячейки XLSX. Проверьте структуру шаблона.') from exc
    finally:book.close()


def resolve(rows,mappings=None,scope=None):
    mappings=mappings or {};goods=list(Goods.objects.order_by('pk'));branches=list(Subdivision.all_objects.order_by('subdivision_key'))
    good_map={};branch_map={}
    for good in goods:good_map.setdefault(tuple(norm(v) for v in (good.planning_group_sales,good.group,good.brand)),[]).append(good)
    for branch in branches:
        for name in {norm(branch.subdivision_key),norm(branch.subdivision_name)}:branch_map.setdefault(name,[]).append(branch)
    resolved=[];errors=[];missing_goods={};missing_branches={};seen=set()
    identities={};branch_names={}
    for block in scope or []:
        for name in block['branches']:
            if name not in branch_names:branch_names[name]=str(len(branch_names))
            bid=branch_names[name];options=branch_map.get(norm(name),[]);chosen=mappings.get('branch_'+bid)
            branch=next((b for b in branches if str(b.pk)==str(chosen)),None) if chosen else options[0] if len(options)==1 else None
            if not branch:missing_branches[bid]=name
    for row in rows:
        key=' | '.join(row['classification'])
        if key not in identities:identities[key]=str(len(identities))
        gid=identities[key]
        candidates=good_map.get(tuple(norm(v) for v in row['classification']),[])
        chosen=mappings.get('goods_'+gid)
        good=next((g for g in goods if str(g.pk)==str(chosen)),None) if chosen else candidates[0] if len(candidates)==1 else None
        if not good:missing_goods[gid]=key
        for branch_name in row['branches']:
            if branch_name not in branch_names:branch_names[branch_name]=str(len(branch_names))
            bid=branch_names[branch_name];options=branch_map.get(norm(branch_name),[]);chosen=mappings.get('branch_'+bid)
            branch=next((b for b in branches if str(b.pk)==str(chosen)),None) if chosen else options[0] if len(options)==1 else None
            if not branch:missing_branches[bid]=branch_name
            if not good or not branch:continue
            identity=(good.pk,branch.pk,row['kind'])
            if identity in seen:errors.append(f'Повторная матрица: {branch.subdivision_key}, {good}, {row["kind"]}.');continue
            seen.add(identity)
            resolved.append({**row,'goods_id':good.pk,'branch_id':branch.pk,'branch':branch.subdivision_key,'goods_label':str(good)})
    coverage=branch_coverage(scope if scope is not None else rows,branches,mappings,branch_names)
    return {'coverage':coverage,'rows':resolved,'errors':errors,'missing_goods':missing_goods,'missing_branches':missing_branches,'goods_options':goods,'branch_options':branches,'ready':bool(resolved) and not errors and not missing_goods and not missing_branches}


@transaction.atomic
def apply(version_id,revision,rows,mappings,formula_id,*,scope=None):
    version=lock_editable(version_id)
    if str(version.revision)!=str(revision):raise ValidationError('Версия изменилась после просмотра. Загрузите файл заново.')
    plan=resolve(rows,mappings,scope)
    if not plan['ready']:raise ValidationError('Сначала сопоставьте все подразделения и товарные матрицы; устраните повторения.')
    formula=VariationCalculate.objects.filter(pk=formula_id).first()
    if not formula and any(not row.get('matrix_export') for row in plan['rows']):raise ValidationError('Выберите формулу для новых ячеек.')
    from .director_formula import resolve as effective_formula
    formulas={f.variation_name:f for f in VariationCalculate.objects.all()}
    kinds={}
    for name in ('Политики','Продажи'):
        matches=list(TypeCoeff.objects.filter(type_coeff_name=name))
        if len(matches)!=1:raise ValidationError('Проверьте справочник видов коэффициентов.')
        kinds[name]=matches[0]
    segments={}
    for segment in SegmentCoeff.objects.all():
        label=norm(segment.segment_name).replace('к','k').replace('o','k')
        if label in ('k0','k1','k2','k3','k4'):
            if label in segments:raise ValidationError('В справочнике повторяется сегмент K0–K4.')
            segments[label]=segment
    if len(segments)!=5:raise ValidationError('В справочнике нужны все K0–K4.')
    existing={(r.subdivision_id,r.goods_id,r.type_coeff_id,r.segment_id):r for r in SubdivisionCoeff.objects.filter(version=version,type_coeff__in=kinds.values()).select_related('goods','subdivision','variation_calculate')}
    created=[];updated=[]
    for row in plan['rows']:
        if len(row['rates'])!=5:raise ValidationError('В матрице должны быть K0–K4.')
        for index,raw in enumerate(row['rates']):
            if raw is None and row.get('matrix_export'):continue
            value=rate(raw,precision=12 if row.get('matrix_export') else 6)
            identity=(row['branch_id'],row['goods_id'],kinds[row['kind']].pk,segments['k'+str(index)].pk)
            cell=existing.get(identity)
            file_formula=row.get('formulas',[None]*5)[index]
            selected=formulas.get(file_formula) if file_formula else None
            if file_formula and not selected:raise ValidationError(f'Не найдена формула «{file_formula}».')
            if cell:
                changed=Decimal(str(cell.motivation_coeff))!=Decimal(value)
                if changed:cell.motivation_coeff=float(value)
                if selected and file_formula!=effective_formula(version,cell.goods,cell.subdivision.subdivision_key,cell,kind=row['kind'])[0]:cell.variation_calculate=selected;changed=True
                if changed:updated.append(cell)
            else:
                if not selected and not formula:raise ValidationError('Выберите формулу для новых ячеек без формулы в файле.')
                created.append(SubdivisionCoeff(version=version,subdivision_id=identity[0],goods_id=identity[1],type_coeff_id=identity[2],segment_id=identity[3],motivation_coeff=float(value),manager_coeff=1,variation_calculate=selected or formula))
    if created:SubdivisionCoeff.objects.bulk_create(created,batch_size=100)
    if updated:SubdivisionCoeff.objects.bulk_update(updated,['motivation_coeff','variation_calculate'],batch_size=100)
    return {'created':len(created),'updated':len(updated),'matrices':len(plan['rows'])}



def branch_coverage(scope,branches,mappings,branch_names):
    known={}
    for branch in branches:
        for name in {norm(branch.subdivision_key),norm(branch.subdivision_name)}:known.setdefault(name,[]).append(branch)
    present=set();unknown=set();by_kind={kind:set() for kind in ('Политики','Продажи')}
    for block in scope:
        for name in block['branches']:
            candidates=known.get(norm(name),[])
            branch=candidates[0] if len(candidates)==1 else None
            bid=branch_names.get(name)
            chosen=mappings.get('branch_'+str(bid)) if bid is not None else None
            if chosen:branch=next((b for b in branches if str(b.pk)==str(chosen)),None)
            if branch:
                present.add(branch.pk);by_kind[block['kind']].add(branch.pk)
            else:unknown.add(name)
    return {'directory_count':len(branches),'file_count':len({norm(name) for block in scope for name in block['branches']}),
        'matched':[b.subdivision_key for b in branches if b.pk in present],
        'missing_from_file':[b.subdivision_key for b in branches if b.pk not in present],
        'unknown_in_file':sorted(unknown),
        'by_kind':[{'kind':kind,'missing':[b.subdivision_key for b in branches if b.pk not in ids]} for kind,ids in by_kind.items()]}


def has_subdivision_blocks(upload):
    if not upload or not str(upload.name).lower().endswith('.xlsx'):return False
    book=None
    try:
        if upload.size>10*1024*1024:return True
        content=upload.read()
        with zipfile.ZipFile(BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist())>100*1024*1024:return True
        book=load_workbook(BytesIO(content),read_only=True,data_only=True,keep_links=False)
        for sheet in book:
            from .export_matrix_import import is_matrix
            if sheet.title=='Подразделения' and is_matrix(sheet):return True
            for index,row in enumerate(sheet.iter_rows(max_row=min(sheet.max_row,2000),max_col=256,values_only=True)):
                if index>0 and len(row)>3 and norm(row[1])=='группаплановпродаж' and norm(row[2])=='группаerp' and any(norm(v) in ('политики','продажи') for v in row):return True
        return False
    except Exception:return False
    finally:
        if book:book.close()
        upload.seek(0)
