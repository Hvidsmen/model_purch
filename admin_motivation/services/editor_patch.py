"""Scope-validated staged formulas and the shared atomic table-save implementation."""
from django.core.exceptions import ValidationError
from ..models import GlobalCoeff, SubdivisionCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff, VariationCalculate, Goods


def formulas(version, data, subdivision=None, employee=None):
    core_model = SubdivisionCoeff if subdivision else GlobalCoeff
    dir_model = SubdivisionDirectorCoeff if subdivision else GlobalDirectorCoeff
    core_scope = {'version':version, **({'subdivision':subdivision} if subdivision else {})}
    dir_scope = {**core_scope, **({'employee':employee} if subdivision else {})}
    core, directors = {}, {}
    for key,value in data.items():
        if key.startswith(('formula_coeff=','director_formula=')):
            suffix=key.split('=',1)[1]
            if not suffix.isdigit():raise ValidationError('Некорректная ячейка формулы.')
            target=core if key.startswith('formula_coeff=') else directors
            try:target[int(suffix)]=int(value) if value not in ('',None) else None
            except (ValueError,TypeError):raise ValidationError('Выберите формулу из справочника.')
    if not core and not directors:return {},{}
    if str(data.get('revision')) != str(version.revision):raise ValidationError('Версия изменилась. Обновите страницу перед сохранением формул.')
    ids={value for value in [*core.values(),*directors.values()] if value is not None}
    if set(VariationCalculate.objects.filter(pk__in=ids).values_list('pk',flat=True)) != ids:raise ValidationError('Формула удалена или не найдена.')
    deleted={int(key.split('=',1)[1]) for key in data if key.startswith('delete_coeff=')}
    rows={row.pk:row for row in core_model.objects.filter(**core_scope,pk__in=core)}
    if rows.keys()!=core.keys():raise ValidationError('Формула содержит ставку другой версии или подразделения.')
    if any(value is None for value in core.values()):raise ValidationError('Для политик и продаж выберите явную формулу.')
    core={pk:value for pk,value in core.items() if rows[pk].goods_id not in deleted and rows[pk].variation_calculate_id != value}
    if directors:
        if subdivision and (not employee or employee.is_archived or not employee.subdivisions.filter(pk=subdivision.pk).exists()):
            # Unchanged empty placeholders may be present without an employee.
            if any(value is not None for value in directors.values()):raise ValidationError('Выберите действующего директора подразделения.')
            return core,{}
        if set(Goods.objects.filter(pk__in=directors).values_list('pk',flat=True)) != directors.keys():raise ValidationError('Товар формулы директора не найден.')
        existing={row.goods_id:row for row in dir_model.objects.filter(**dir_scope,goods_id__in=directors)}
        from ..views import coefficient_number
        changed={}
        for good,value in directors.items():
            if good in deleted:continue
            row=existing.get(good)
            if row and row.variation_calculate_id==value or not row and value is None:continue
            if not row and not any(coefficient_number(data[key]) != 0 for key in data if key.startswith(f'director_coeff={good}=')):
                raise ValidationError('У товара нет директорских ставок. Задайте ставки или создайте их через проверку покрытия.')
            changed[good]=value
        directors=changed
    if (core or directors) and not version.is_editable:raise ValidationError('Формулы можно менять только в текущем черновике.')
    return core,directors


def save_table(version,data,verification,subdivision=None,employee=None):
    from .coefficient_groups import save_global_directors
    deleted=[key.split('=',1)[1] for key in data if key.startswith('delete_coeff=')]
    save_global_directors(version,data,deleted,subdivision=subdivision,employee=employee)
    if subdivision:
        from ..models import SubdivisionManagerCoeff
        from ..views import coefficient_number
        for key in data:
            if key.startswith('kind_coeff='):
                SubdivisionManagerCoeff.objects.update_or_create(version=version,subdivision=subdivision,kind_id=int(key.split('=')[1]),defaults={'coeff':coefficient_number(data[key])})
    pending={}
    for query,field,expected in verification.checks:
        row=query.first()
        if row is None:raise ValidationError('Запись для сохранения не найдена. Правки не сохранены.')
        identity=(query.model,row.pk)
        target=pending.setdefault(identity,row)
        setattr(target,field,expected)
        if field=='motivation_coeff':target.manager_coeff=1
    for row in pending.values():row.save()
    for query in verification.deletions:query.delete()
    return verification.verify()
