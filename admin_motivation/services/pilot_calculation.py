"""Disable early premium projections, preserving the full-year input for Q4/year.

Only the portal procedure copy is changed. Unknown output structures fail closed.
"""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql
from .manager_correction import TABLE

PERIODS=('4','5')
NOTICE='Пилот: рассчитываются только премия за 4-й квартал (нарастающим итогом) и годовая премия. Доплат за предыдущие кварталы нет.'
MARKER='-- Portal pilot premiums: Q4 and annual only.'


def patch(definition):
    if MARKER in definition:
        raise ValidationError('Ограничение пилотных периодов уже присутствует в исходной процедуре. Проверьте совместимость.')
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
    # Keep the statement intact but do not execute it. The manager-correction
    # patch must run first, so IF guards only the carry INSERT, not corrections.
    edits.append((carry[0].start(),carry[0].start(),MARKER+'\nIF 1=0\n'))
    for start,end,value in sorted(edits,reverse=True):
        definition=definition[:start]+value+definition[end:]
    return definition
