"""Approved Moscow/Thermotrade transition, applied only to portal copies."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql

MOSCOW = 'Д-Москва'
THERMOTRADE = 'ТЕРМОТРЕЙД'
SEPARATE = 'separate_2026'
RULE_VERSION = 'moscow-tt-q4-policy-heat-sales-dpp-v2'


def patch(definition):
    """Keep physical inputs; apply the shared rules before motivation windows."""
    mask = mask_sql(definition)
    deletes = list(re.finditer(r'\bDELETE\s+(?:FROM\s+)?#pf_base\b', mask, re.I))
    if not deletes:
        raise ValidationError('Не найдена база расчёта для правил участия.')
    at = deletes[0].start()
    from .motivation_exclusions import sql_allowed
    definition = definition[:at] + '\n-- Shared motivation participation rules.\nDELETE FROM #pf_base WHERE NOT ('+sql_allowed()+');\n' + definition[at:]
    # Restrict the input set BEFORE window functions, for both the Q4 and
    # annual output SELECT. Never filter already-calculated annual windows.
    labels = list(re.finditer(r"N?'(?P<period>[1-4]\s+Квартал[^']*|Год)'\s+(?:AS\s+)?\[?КварталМотивации\]?", definition, re.I))
    if len(labels) != 6:
        raise ValidationError('Не распознаны периоды переходного расчёта 2026.')
    edits = []
    for index in (3, 4):
        segment = definition[labels[index].end():labels[index + 1].start()]
        matches = list(re.finditer(r'\bFROM\s+#pf_base\s+WHERE\s+Квартал\s+IN\s*\(\s*1\s*,\s*2\s*,\s*3\s*,\s*4\s*\)', segment, re.I))
        if len(matches) != 1:
            raise ValidationError('Не распознано годовое окно для перехода на IV квартал.')
        match = matches[0]
        edits.append((labels[index].end()+match.start(), labels[index].end()+match.end(),
                      'FROM #pf_base WHERE (Квартал=4 OR @FactYear<>2026)'))
    for start, end, value in reversed(edits):
        definition = definition[:start] + value + definition[end:]
    return definition


def separate_coefficients(definition):
    """Keep source subdivision labels but use the approved merged rates."""
    definition = definition.replace('FROM #pf_base WHERE (Квартал=4 OR @FactYear<>2026)',
                                    'FROM #pf_base WHERE 1=0 AND (Квартал=4 OR @FactYear<>2026)')
    definition, count = re.subn(
        r'\b(?P<alias>sub|s)\.SubdivisionName\s*=\s*sc\.Subdivision',
        lambda m: "COALESCE((SELECT m.TargetName FROM motivation.PortalSubdivisionMerge m WHERE m.SourceName="
        + m['alias'] + '.SubdivisionName),' + m['alias'] + '.SubdivisionName)=sc.Subdivision',
        definition, flags=re.I)
    # The optimized source shares one fact and one plan coefficient join;
    # the legacy source has two of each.
    if count not in (2, 4):
        raise ValidationError('Не распознаны соединения ставок раздельного сценария.')
    definition, director_count = re.subn(
        r'\bdirectors\.Subdivision\s*=\s*(?P<alias>sub|s)\.SubdivisionName',
        lambda m: 'directors.Subdivision=COALESCE((SELECT m.TargetName FROM motivation.PortalSubdivisionMerge m WHERE m.SourceName='
        + m['alias'] + '.SubdivisionName),' + m['alias'] + '.SubdivisionName)',
        definition, flags=re.I)
    if re.search(r'#director_employees\b', definition, re.I) and director_count != 2:
        raise ValidationError('Не распознаны назначения директоров раздельного сценария.')
    for column in ('r.[Подразделение]', 'f.[Подразделение]'):
        definition = definition.replace('c.Subdivision='+column,
            'c.Subdivision=COALESCE((SELECT m.TargetName FROM motivation.PortalSubdivisionMerge m WHERE m.SourceName='
            +column+'),'+column+')')
    return definition

