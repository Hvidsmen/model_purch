"""Revision-bound previews and atomic saves for inline month distribution."""
from copy import deepcopy
from decimal import Decimal
import json

from django.core import signing
from django.core.exceptions import ValidationError
from .split_engine import allocate, HUNDRED, number, planning_months, precise, quantity, stored
from .split_distribution_history import resolve_proposal, metadata

SALT = 'split-inline-months'


def selected(scenario, gid, cid):
    group = scenario.state['groups'].get(gid)
    if group is None or cid not in group['channels']:
        raise ValidationError('Группа или канал отсутствует в сценарии. Обновите страницу.')
    return group


def preview(scenario, gid, cid):
    group = selected(scenario,gid,cid)
    values,basis = resolve_proposal(scenario,group,scenario.state['channels'][cid]['name'],'months')
    basis = metadata(basis)
    token = signing.dumps({'identity':[scenario.pk,scenario.revision,gid,cid],
                           'values':values,'basis':basis},salt=SALT,compress=True)
    return {'values':values,'basis':basis,'token':token}


@precise
def apply_grid(scenario, raw):
    if len(raw) > 2_000_000:
        raise ValidationError('Слишком большой блок распределения.')
    try:
        rows = json.loads(raw)
    except (ValueError,TypeError):
        raise ValidationError('Некорректный формат распределения.')
    limit = sum(len(group['channels']) for group in scenario.state['groups'].values())
    if not isinstance(rows,list) or not rows or len(rows) > limit:
        raise ValidationError('Нет изменённых строк либо список строк некорректен.')
    months = planning_months(scenario)
    state,seen = deepcopy(scenario.state),set()
    for row in rows:
        if not isinstance(row,dict) or not all(isinstance(row.get(key),str) for key in ('gid','cid','mode','unit')):
            raise ValidationError('Некорректная строка распределения.')
        gid,cid,mode,unit = (row[key] for key in ('gid','cid','mode','unit'))
        original = selected(scenario,gid,cid)
        if (gid,cid) in seen:
            raise ValidationError('Одна строка передана несколько раз.')
        seen.add((gid,cid))
        if mode not in ('fact','equal','manual') or unit not in ('percent','quantity') or (mode != 'manual' and unit != 'percent'):
            raise ValidationError('Выберите допустимый способ распределения.')
        values = row.get('values')
        if not isinstance(values,dict) or set(values) != set(months):
            raise ValidationError('Передайте значения всех плановых месяцев.')
        parsed = {month:number(values[month]) for month in months}
        target = HUNDRED if unit == 'percent' else quantity(scenario,original['channels'][cid])
        if (unit == 'percent' and any(value > HUNDRED for value in parsed.values())) or sum(parsed.values()) != target:
            raise ValidationError(original['ref']['name']+' · '+scenario.state['channels'][cid]['name']+
                (': сумма должна быть ровно 100%.' if unit == 'percent' else ': месячные количества должны совпадать с планом.'))
        source = None
        if mode == 'equal':
            expected = dict(zip(months,allocate(HUNDRED,[Decimal(1)]*len(months))))
            if parsed != expected:
                raise ValidationError('Равномерное распределение изменено. Выберите «Вручную».')
        if mode == 'fact':
            try:
                signed = signing.loads(row.get('token',''),salt=SALT,max_age=86400)
                if signed['identity'] != [scenario.pk,scenario.revision,gid,cid] or set(signed['values']) != set(months):
                    raise signing.BadSignature
                if parsed != {month:Decimal(value) for month,value in signed['values'].items()}:
                    raise signing.BadSignature
                source = signed['basis']
            except (signing.BadSignature,KeyError,TypeError,ValueError):
                raise ValidationError('Распределение из факта устарело или изменено. Получите его заново либо выберите «Вручную».')
        group = state['groups'][gid]
        line = group['channels'][cid]
        line['allocation'],line['distribution_mode'] = unit,mode
        line['month_quantities' if unit == 'quantity' else 'months'] = {month:stored(value) for month,value in parsed.items()}
        sources = group.setdefault('distribution_sources',{})
        if source:
            sources['months:'+cid] = source
        else:
            sources.pop('months:'+cid,None)
    return state,len(rows)
