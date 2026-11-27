"""Display the director formula with the same precedence as group-rule SQL."""
from .sql_export import director_variation
from django.core.exceptions import ValidationError


def rule_for(version,good,branch='',kind='Директора'):
    relevant=[rule for rule in version.planning_rules if rule.get('kind')==kind
        and rule.get('planning','').strip().casefold()==good.planning_group_sales.strip().casefold()
        and rule.get('subdivision','').strip().casefold() in ('',branch.strip().casefold())]
    relevant.sort(key=lambda rule:rule.get('subdivision','').strip().casefold()!=branch.strip().casefold())
    return relevant[0] if relevant else {}


def core_formulas(version,rows=None):
    if rows is None:
        from ..models import SubdivisionCoeff
        rows=SubdivisionCoeff.objects.filter(version=version).select_related('subdivision','type_coeff','variation_calculate')
    result={}
    for row in rows:
        if row.type_coeff.type_coeff_name not in ('Политики','Продажи'):continue
        if row.variation_calculate_id:result.setdefault((row.subdivision.subdivision_key,row.goods_id),set()).add(row.variation_calculate.variation_name)
    return result


def resolve(version,good,branch='',row=None,*,kind='Директора',inherited=None):
    # A local rule without a formula shadows the global rule, just as TOP(1).
    rule=rule_for(version,good,branch,kind)
    if rule.get('formula'):return rule['formula'],'Правило группы подразделения' if rule.get('subdivision') else 'Глобальное правило группы'
    if row is not None and row.variation_calculate_id:return row.variation_calculate.variation_name,'Настройка директора'
    if kind=='Директора' and branch:
        formulas=(core_formulas(version) if inherited is None else inherited).get((branch,good.pk),set())
        if len(formulas)>1:raise ValidationError(f'{branch} · {good}: у политик и продаж разные формулы. Укажите отдельную формулу директора.')
        if formulas:return next(iter(formulas)),'Формула товара подразделения'
    if version.fallback_variation:return version.fallback_variation,'Формула версии по умолчанию'
    return director_variation(good),'Прежнее правило по группе товара'
