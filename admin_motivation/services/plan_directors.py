"""Persist director identities and coefficients alongside the dated plan snapshot."""
from decimal import Decimal
from ..models import DirectorEmployee
from .director_comparison import DirectorIndex
from .sales_plans import SEGMENTS, number


class PlanDirectors:
    def __init__(self, versions):
        ids = [version.pk for version in versions]
        self.local = DirectorIndex(ids, local=True)
        self.global_ = DirectorIndex(ids)
        self.names = {row.pk: (row.employee_code, row.full_name) for row in DirectorEmployee.objects.all()}
        self.global_rows = {}

    def calculate(self, line, version):
        key = (line.plan_date, line.planning_group_sales, line.group, line.brand)
        if not line.subdivision:
            employees = list(self.global_rows.pop(key, {}).values())
            return self.snapshot(employees)
        amounts = [number(line.segment_amounts[segment]) for segment in SEGMENTS]
        employees = []
        for staff in sorted(self.local.employees.get((version, line.subdivision), ())):
            values, source = self.local.coefficients(version, line.subdivision, staff, *key[1:])
            employee = self.employee(staff, version, values, source)
            employee['subdivisions'] = [line.subdivision]
            employee['segment_amounts'] = list(map(str, amounts))
            employee['motivation'] = str(sum((value * usd for value, usd in zip(values, amounts)), Decimal(0)))
            employees.append(employee)
            if 0 in self.global_.employees.get((version, ''), ()):
                global_values, global_source = self.global_.coefficients(version, '', 0, *key[1:])
            else:
                global_values, global_source = [Decimal(0)] * 5, 'missing'
            global_employee = self.global_rows.setdefault(key, {}).setdefault(staff, self.employee(staff, version, global_values, global_source))
            global_employee['subdivisions'].append(line.subdivision)
            global_employee['segment_amounts'] = [str(Decimal(old) + new) for old, new in zip(global_employee['segment_amounts'], amounts)]
            global_employee['motivation'] = str(Decimal(global_employee['motivation']) + sum((value * usd for value, usd in zip(global_values, amounts)), Decimal(0)))
        return self.snapshot(employees)

    def employee(self, staff, version, values, source):
        code, name = self.names[staff]
        return {'employee_id': staff, 'employee_code': code, 'employee_name': name,
                'version_id': version, 'coefficients': list(map(str, values)), 'source': source,
                'subdivisions': [], 'segment_amounts': ['0'] * 5, 'motivation': '0'}

    @staticmethod
    def snapshot(employees):
        total = sum((Decimal(employee['motivation']) for employee in employees), Decimal(0))
        return total, {'employees': employees, 'motivation': str(total)}
