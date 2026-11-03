"""Current director assignments shared by reports and coefficient export."""
from xml.etree.ElementTree import Element, SubElement, tostring
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
    # XML methods work at DWH compatibility level 110; OPENJSON requires 130.
    roster = Element('roster')
    for sub, author in sorted(records):
        record = SubElement(roster, 'director')
        SubElement(record, 'subdivision').text = sub
        SubElement(record, 'author').text = author
    return tostring(roster, encoding='unicode')


REPORT_CONDITION = """(
 [ТипКоэф] <> N'Директора' OR EXISTS (
 SELECT 1
 FROM (SELECT CAST(@PortalDirectorRoster AS xml) AS document) roster_document
 CROSS APPLY roster_document.document.nodes('/roster/director') AS roster_nodes(record)
 CROSS APPLY (SELECT
   roster_nodes.record.value('(subdivision/text())[1]', 'nvarchar(255)') AS Subdivision,
   roster_nodes.record.value('(author/text())[1]', 'nvarchar(255)') AS Author
 ) roster
 WHERE roster.Subdivision=[Подразделение]
 AND LTRIM(RTRIM(roster.Author))=LTRIM(RTRIM([АвторСделки]))
 ))"""

