"""Canonical subdivision names shared by portal calculations and DWH reports."""
import re
from django.core.exceptions import ValidationError
from ..models import Subdivision


def rules():
    return [(row.subdivision_key, row.merged_into.subdivision_key, row.merge_policy)
            for row in Subdivision.all_objects.filter(merged_into__isnull=False).select_related('merged_into')]


def mapping():
    return {source: target for source, target, policy in rules()}


def canonical(value, names):
    return names.get(value, value)


def expand(values, names):
    values = values if isinstance(values, list) else [values]
    selected = list(dict.fromkeys(canonical(value, names) for value in values if value is not None))
    return list(dict.fromkeys([*selected, *[source for source, target in names.items() if target in selected]]))


def coefficient_rows(rows, sub_index, key_indexes):
    config = rules()
    if not config:
        yield from rows
        return
    names = {source: target for source, target, policy in config}
    preferred_sources = {source for source, target, policy in config if policy == 'source'}
    def rank(original, target):
        return (0 if original in preferred_sources else 1 if original == target else 2, str(original).casefold(), str(original))
    chosen = {}
    for raw in rows:
        row = list(raw)
        original = row[sub_index]
        row[sub_index] = canonical(original, names)
        key = tuple(row[index] for index in key_indexes)
        if key in chosen:
            old_original, old_row = chosen[key]
            if original == old_original:
                raise ValidationError('Неоднозначные коэффициенты для одной классификации.')
            if rank(original, row[sub_index]) >= rank(old_original, row[sub_index]):
                continue
        chosen[key] = (original, tuple(row))
    yield from (row for original, row in chosen.values())


def report_parameters(sql, values):
    names = mapping()
    if not names:
        return sql, values
    values = dict(values)
    for key in ('Subdivision', 'Division'):
        if values.get(key):
            values[key] = expand(values[key], names)
            sql = re.sub(r'([\w\[\].]+)\s*=\s*@' + key + r'\b', r'\1 IN (@' + key + ')', sql, flags=re.I)
    return sql, values


def report_rows(rows):
    names = mapping()
    if names:
        for row in rows:
            for field in ('Подразделение', 'Subdivision', 'SubdivisionName'):
                if field in row:
                    original = row[field]
                    if field == 'Подразделение':
                        row['_source_subdivision'] = original
                    row[field] = canonical(original, names)
    return rows
