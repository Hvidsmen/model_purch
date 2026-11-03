"""Main variant: Q4-only premium amounts, preserving plan/fact execution windows.

Only the portal procedure copy is changed. Unknown output structures fail closed.
"""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql
from .manager_correction import TABLE

PERIODS=('4','5')
NOTICE='Квартальная и годовая суммы премии учитывают только IV квартал. Доплат за предыдущие кварталы нет.'
MARKER='-- Portal pilot premiums: Q4 and annual only.'


def normalize_q4_rur(definition):
    """Correct the known Q3 typo in the Q4 possible RUB amount only."""
    mask = mask_sql(definition)
    labels = list(re.finditer(r"N?'(?P<period>[1-4]\s+Квартал[^']*|Год)'\s+(?:AS\s+)?\[?КварталМотивации\]?", definition, re.I))
    q4 = next((item for item in labels if item['period'].startswith('4')), None)
    year = next((item for item in labels if item['period'].casefold() == 'год'), None)
    if q4 is None or year is None:
        return definition
    starts = [item.start() for item in re.finditer(r'\bSELECT\b', mask[:q4.start()], re.I)]
    ends = [item.start() for item in re.finditer(r'\bSELECT\b', mask[:year.start()], re.I)]
    if not starts or not ends:
        return definition
    start, end = starts[-1], ends[-1]
    segment = re.sub(r'IIF\s*\(\s*Квартал\s*=\s*3\s*,\s*СуммаМотивацииRUR\s*,\s*0\s*\)',
                     'IIF(Квартал=4,СуммаМотивацииRUR,0)', definition[start:end], flags=re.I)
    return definition[:start] + segment + definition[end:]


def patch(definition):
    if MARKER in definition:
        raise ValidationError('Ограничение пилотных периодов уже присутствует в исходной процедуре. Проверьте совместимость.')
    definition = normalize_q4_rur(definition)
    mask=mask_sql(definition)
    pattern=re.compile(r"N?'(?P<period>[1-4]\s+Квартал[^']*|Год)'\s+(?:AS\s+)?\[?КварталМотивации\]?",re.I)
    projections=[match for match in pattern.finditer(definition) if re.search(r'КварталМотивации',mask[match.start():match.end()],re.I)]
    codes=['5' if match['period'].casefold()=='год' else match['period'][0] for match in projections]
    if codes!=['1','2','3','4','5','5']:
        raise ValidationError('Не распознаны выходные периоды расчёта: нужны кварталы 1–4, год и годовая доплата. Расчёт не запущен.')
    edits=[]
    predicate=re.compile(r'\bWHERE\s+\[?Квартал\]?\s*(?:=\s*[1-3](?!\d)|IN\s*\(\s*[1-3](?:\s*,\s*[1-3])*\s*\))',re.I)
    for index in range(3):
        start,end=projections[index].end(),projections[index+1].start()
        matches=list(predicate.finditer(mask,start,end))
        if len(matches)!=1:
            raise ValidationError('Не найдена однозначная граница квартального расчёта пилота. Расчёт не запущен.')
        where=matches[0]
        edits.append((where.start(),where.start()+5,'WHERE 1=0 AND'))
    carry=list(re.finditer(r'\bINSERT\s+(?:INTO\s+)?'+TABLE+r'(?!\w)',mask,re.I))
    if len(carry)!=1 or not projections[4].start()<carry[0].start()<projections[5].start():
        raise ValidationError('Не распознана годовая доплата. Расчёт не запущен.')
    # Restrict annual money, not the window inputs used for plan execution.
    # Q4 eligibility still uses the existing cumulative plan/fact conditions.
    annual_start, annual_end = projections[3].end(), carry[0].start()
    for field in ('СуммаМотивацииUSDГод', 'СуммаМотивацииRURГод'):
        references = list(re.finditer(r'(?<![\w])\[?' + field + r'\]?(?![\w])', mask[annual_start:annual_end], re.I))
        if not references:
            raise ValidationError('Не найдена сумма годовой премии для ограничения IV кварталом. Расчёт не запущен.')
        for reference in references:
            start, end = annual_start + reference.start(), annual_start + reference.end()
            edits.append((start, end, 'IIF(Квартал=4,' + definition[start:end] + ',0)'))
    # Keep the statement intact but do not execute it. The manager-correction
    # patch must run first, so IF guards only the carry INSERT, not corrections.
    edits.append((carry[0].start(),carry[0].start(),MARKER+'\nIF 1=0\n'))
    for start,end,value in sorted(edits,reverse=True):
        definition=definition[:start]+value+definition[end:]
    return definition
