from decimal import Decimal
from django.core.exceptions import ValidationError
from ..models import GlobalDirectorCoeff, SubdivisionDirectorCoeff, Goods


class DirectorIndex:
    def __init__(self, versions, local=False, current=None, changes=None, subdivision=None, employee=None, deleted=None):
        from .comparison import coefficient_input
        model = SubdivisionDirectorCoeff if local else GlobalDirectorCoeff
        rows = model.objects.filter(version_id__in=versions).select_related('goods')
        if local:
            rows = rows.select_related('subdivision')
        self.cells, self.minimum, self.table_minimum, self.employees = {}, {}, {}, {}
        data = []
        for row in rows:
            sub = row.subdivision.subdivision_key if local else ''
            staff = row.employee_id if local else 0
            if current and row.version_id == current.pk and (not local or (employee and staff == employee.pk)) and (subdivision.subdivision_key if subdivision else '') == sub:
                if row.goods_id in (deleted or []):
                    continue
            values = [getattr(row, f'k{i}') for i in range(5)]
            if current and row.version_id == current.pk and (subdivision.subdivision_key if subdivision else '') == sub and (not local or (employee and staff == employee.pk)):
                values = [(changes or {}).get(f'{row.goods_id}:{i}', value) for i, value in enumerate(values)]
            data.append((row.version_id, sub, staff, row.goods, values))
        # New director cells can be edited on existing policy/sales goods.
        if changes and current and (not local or employee):
            ids = {int(key.split(':')[0]) for key in changes}
            present = {good.pk for version, sub, staff, good, values in data if version == current.pk and staff == (employee.pk if local else 0) and sub == (subdivision.subdivision_key if subdivision else '')}
            for good in Goods.objects.filter(pk__in=ids - present):
                data.append((current.pk, subdivision.subdivision_key if local else '', employee.pk if local else 0, good,
                    [changes.get(f'{good.pk}:{i}', Decimal(0)) for i in range(5)]))
        for version, sub, staff, good, values in data:
            scope = (version, sub, staff, good.planning_group_sales, good.group, good.brand)
            if scope in self.cells:
                raise ValidationError('Неоднозначные коэффициенты директора для товарной классификации.')
            values = [coefficient_input(value) for value in values]
            self.cells[scope] = values
            self.employees.setdefault((version, sub), set()).add(staff)
            minimum = min(values)
            key = (version, sub, staff, good.planning_group_sales)
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
