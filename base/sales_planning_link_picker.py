"""Directory attributes for the group-link picker; allowed IDs remain form-owned."""
from .sales_planning_scope import split_goods


FIELDS = [('brand', 'Марка'), ('sales_plan_group', 'Группа планов продаж'),
          ('outdoor_block_type', 'INV / On-Off'), ('manufacturer', 'Производитель')]


def picker_context(form):
    sections, groups = [], {}
    for name in ('related_groups', 'replacement_groups'):
        field = form[name]
        choices = list(form.fields[name].queryset)
        selected = {str(value) for value in (field.value() or [])}
        groups.update({group.pk: group for group in choices})
        sections.append({'name': name, 'title': field.label, 'field': field,
                         'count': sum(str(group.pk) in selected for group in choices)})
    metadata = {str(pk): {'active': group.is_active, **{field: set() for field, label in FIELDS}}
                for pk, group in groups.items()}
    owners = {}
    for pk, group in groups.items():
        for raw in group.source_names:
            owners.setdefault(raw, set()).add(str(pk))
    goods = split_goods(include_inactive=True).filter(planning_group__in=owners).order_by().values('planning_group', *[field for field, label in FIELDS])
    for row in goods.iterator():
        for pk in owners.get(row['planning_group'], ()):
            for field, label in FIELDS:
                if row[field]:
                    metadata[pk][field].add(row[field])
    options = []
    for field, label in FIELDS:
        options.append({'name': field, 'label': label,
                        'values': sorted({value for entry in metadata.values() for value in entry[field]})})
    for entry in metadata.values():
        for field, label in FIELDS:
            entry[field] = sorted(entry[field])
    return {'link_sections': sections, 'link_metadata': metadata, 'link_filters': options}
