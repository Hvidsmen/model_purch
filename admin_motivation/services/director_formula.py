"""Display the director formula with the same precedence as group-rule SQL."""
from .sql_export import director_variation


def rule_for(version,good,branch=''):
    relevant=[rule for rule in version.planning_rules if rule.get('kind')=='Директора'
        and rule.get('planning','').strip().casefold()==good.planning_group_sales.strip().casefold()
        and rule.get('subdivision','').strip().casefold() in ('',branch.strip().casefold())]
    relevant.sort(key=lambda rule:rule.get('subdivision','').strip().casefold()!=branch.strip().casefold())
    return relevant[0] if relevant else {}


def resolve(version,good,branch='',row=None):
    # A local rule without a formula shadows the global rule, just as TOP(1).
    rule=rule_for(version,good,branch)
    if rule.get('formula'):return rule['formula'],'Правило группы подразделения' if rule.get('subdivision') else 'Глобальное правило группы'
    if row is not None and row.variation_calculate_id:return row.variation_calculate.variation_name,'Настройка директора'
    if version.fallback_variation:return version.fallback_variation,'Формула версии по умолчанию'
    return director_variation(good),'Прежнее правило по группе товара'
