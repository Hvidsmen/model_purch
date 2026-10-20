from decimal import Decimal
from django.core.exceptions import ValidationError
from ..models import GlobalDirectorCoeff, SubdivisionDirectorCoeff, Goods


class DirectorIndex:
    def __init__(self, versions, local=False, current=None, changes=None, subdivision=None, employee=None, deleted=None):
        from .comparison import coefficient_input
        model = SubdivisionDirectorCoeff if local else GlobalDirectorCoeff
        # Scalar streaming avoids building a model plus related Goods/Subdivision
        # objects for every cell, and never retains the complete queryset cache.
        fields = ['version_id', 'goods_id', 'goods__planning_group_sales', 'goods__group', 'goods__brand',
                  *[f'k{i}' for i in range(5)]]
        if local:
            fields += ['subdivision__subdivision_key', 'employee_id']
        rows = model.objects.filter(version_id__in=versions).order_by().values_list(*fields)
        self.cells, self.minimum, self.table_minimum, self.employees = {}, {}, {}, {}
        target_sub = subdivision.subdivision_key if subdivision else ''
        target_staff = employee.pk if employee else 0
        present = set()

        def inputs():
            for row in rows.iterator(chunk_size=2000):
                version, goods_id, pg, group, brand = row[:5]
                values = row[5:10]
                sub, staff = row[10:] if local else ('', 0)
                matching = current and version == current.pk and target_sub == sub and (not local or (employee and staff == target_staff))
                if matching:
                    if goods_id in (deleted or []):
                        continue
                    present.add(goods_id)
                    values = [(changes or {}).get(f'{goods_id}:{i}', value) for i, value in enumerate(values)]
                yield version, sub, staff, pg, group, brand, values
            # Preview edits can introduce director cells on existing policy/sales goods.
            if changes and current and (not local or employee):
                ids = {int(key.split(':')[0]) for key in changes}
                for goods_id, pg, group, brand in Goods.objects.filter(pk__in=ids - present).values_list(
                        'pk', 'planning_group_sales', 'group', 'brand'):
                    yield current.pk, target_sub, target_staff, pg, group, brand, [
                        changes.get(f'{goods_id}:{i}', Decimal(0)) for i in range(5)]

        for version, sub, staff, pg, group, brand, values in inputs():
            scope = (version, sub, staff, pg, group, brand)
            if scope in self.cells:
                raise ValidationError('Неоднозначные коэффициенты директора для товарной классификации.')
            values = [coefficient_input(value) for value in values]
            self.cells[scope] = values
            self.employees.setdefault((version, sub), set()).add(staff)
            minimum = min(values)
            key = (version, sub, staff, pg)
            self.minimum[key] = min(self.minimum.get(key, minimum), minimum)
            key = (sub, staff)
            self.table_minimum[key] = min(self.table_minimum.get(key, minimum), minimum)

    def motivation(self, version, sub, pg, group, brand, amounts):
        return sum(self.by_employee(version, sub, pg, group, brand, amounts).values(), Decimal(0))

    def by_employee(self, version, sub, pg, group, brand, amounts):
        result = {}
        for staff in self.employees.get((version, sub), ()):
            values, _ = self.coefficients(version, sub, staff, pg, group, brand)
            result[staff] = sum((value * amount for value, amount in zip(values, amounts)), Decimal(0))
        return result

    def coefficients(self, version, sub, staff, pg, group, brand):
        values = self.cells.get((version, sub, staff, pg, group, brand))
        if values is not None:
            return values, 'exact'
        value = self.minimum.get((version, sub, staff, pg))
        if value is not None:
            return [value] * 5, 'group_minimum'
        value = self.table_minimum.get((sub, staff))
        return [value if value is not None else Decimal(0)] * 5, 'table_minimum' if value is not None else 'missing'
