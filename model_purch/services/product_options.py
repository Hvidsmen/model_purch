"""Shared product classification choices, independent of active filters."""
from ..models import PGGoods, GoodsGroup


def classification_options():
    from PlanningSystem.models import PlanningGroupSalesRef, GroupERPRef, BrangRef
    references = {'planning_sales': (PlanningGroupSalesRef, 'planning_group_sales_name'),
                  'group_goods': (GroupERPRef, 'group_ERP_name'), 'brand': (BrangRef, 'brand_name')}
    result = {}
    for field in ('planning_sales', 'group_goods', 'brand'):
        values = set(PGGoods.objects.exclude(**{field: ''}).exclude(**{field + '__isnull': True}).values_list(field, flat=True))
        model, name = references[field]
        values.update(model.objects.exclude(**{name: ''}).values_list(name, flat=True))
        if field == 'group_goods':
            values.update(GoodsGroup.objects.values_list('name', flat=True))
        result[field] = sorted(values, key=str.casefold)
    return result
