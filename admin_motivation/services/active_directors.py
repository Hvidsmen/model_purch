"""Current director assignments shared by reports and coefficient export."""
import json
from ..models import DirectorEmployee
from .subdivision_aliases import mapping, canonical


def bindings():
    names = mapping()
    rows = DirectorEmployee.subdivisions.through.objects.filter(
        directoremployee__is_archived=False
    ).values_list('directoremployee_id', 'directoremployee__employee_code',
                  'directoremployee__full_name', 'subdivision__subdivision_key')
    return sorted([
        {'employee_id': employee_id, 'employee_code': code,
         'full_name': name, 'subdivision': canonical(subdivision, names)}
        for employee_id, code, name, subdivision in rows
    ], key=lambda row: (row['employee_id'], row['subdivision']))


def coefficient_rows(rows):
    active = {(row['employee_id'], row['subdivision']) for row in bindings()}
    names = mapping()
    return [row for row in rows if
            (row.employee_id, canonical(row.subdivision.subdivision_key, names)) in active]


def report_roster():
    from .subdivision_aliases import expand
    names = mapping()
    records = {(branch, row['full_name']) for row in bindings()
               for branch in expand([row['subdivision']], names)}
    return json.dumps([{'subdivision': sub, 'author': author}
                       for sub, author in sorted(records)], ensure_ascii=False)


REPORT_CONDITION = """(
 [ТипКоэф] <> N'Директора' OR EXISTS (
 SELECT 1 FROM OPENJSON(@PortalDirectorRoster)
 WITH (Subdivision nvarchar(255) '$.subdivision', Author nvarchar(255) '$.author') roster
 WHERE roster.Subdivision=[Подразделение]
 AND LTRIM(RTRIM(roster.Author))=LTRIM(RTRIM([АвторСделки]))
 ))"""
