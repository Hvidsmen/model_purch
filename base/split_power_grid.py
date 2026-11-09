"""Inline, group-wide power shares and model parameters, saved atomically."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from django.core import signing
from django.core.exceptions import ValidationError
from .split_engine import allocate, HUNDRED, number, precise, stored
from .split_distribution_history import resolve_proposal, metadata, period

SALT = 'split-inline-powers'


def selected(scenario, gid):
    group = scenario.state['groups'].get(gid)
    if group is None:
        raise ValidationError('Группа отсутствует в сценарии. Обновите страницу.')
    return group


def preview(scenario, gid, cid=''):
    group = selected(scenario,gid)
    values,basis = resolve_proposal(scenario,group,'','powers')
    basis = metadata(basis)
    token = signing.dumps({'identity':[scenario.pk,scenario.revision,gid],'values':values,'basis':basis},salt=SALT,compress=True)
    return {'values':values,'basis':basis,'token':token}


@precise
def apply_grid(scenario, raw):
    if len(raw) > 2_000_000:
        raise ValidationError('Слишком большой блок распределения.')
    try:
        rows = json.loads(raw)
    except (ValueError,TypeError):
        raise ValidationError('Некорректный формат распределения.')
    if not isinstance(rows,list) or not rows or len(rows) > len(scenario.state['groups']):
        raise ValidationError('Нет изменённых строк либо список строк некорректен.')
    state,seen = deepcopy(scenario.state),set()
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('gid'),str):
            raise ValidationError('Некорректная строка распределения.')
        gid,mode = row['gid'],row.get('mode')
        original = selected(scenario,gid)
        if gid in seen:
            raise ValidationError('Одна группа передана несколько раз.')
        seen.add(gid)
        if mode not in ('manual','equal','fact') or row.get('unit','percent') != 'percent':
            raise ValidationError('Выберите допустимый способ распределения мощностей.')
        columns = dict.fromkeys(key for group in scenario.state['groups'].values() for key in group['powers'])
        keys = [key for key in columns if key in original['powers'] and original['powers'][key]['active']]
        values = row.get('values')
        if not keys or not isinstance(values,dict) or set(values) != set(keys):
            raise ValidationError('Передайте доли всех активных мощностей группы.')
        parsed = {key:number(values[key]) for key in keys}
        if any(value > HUNDRED for value in parsed.values()) or sum(parsed.values()) != HUNDRED:
            raise ValidationError(original['ref']['name']+': сумма долей мощностей должна быть ровно 100%.')
        source = None
        if mode == 'equal' and parsed != dict(zip(keys,allocate(HUNDRED,[Decimal(1)]*len(keys)))):
            raise ValidationError('Равномерное распределение изменено. Выберите «Вручную».')
        if mode == 'fact':
            existing = original.get('distribution_sources',{}).get('powers')
            start,end = period(scenario)
            current_basis = existing and existing.get('stock_date') == str(scenario.stock_date) and existing.get('through') == str(end-timedelta(days=1)) and existing.get('first_year') == start.year and existing.get('last_year') == scenario.start.year-1
            unchanged = current_basis and not existing.get('adjusted') and parsed == {key:number(original['powers'][key].get('share'),optional=True) for key in keys}
            if not row.get('token') and unchanged:
                source = deepcopy(existing)
            else:
                try:
                    signed = signing.loads(row.get('token',''),salt=SALT,max_age=86400)
                    if signed['identity'] != [scenario.pk,scenario.revision,gid] or parsed != {key:Decimal(value) for key,value in signed['values'].items()}:
                        raise signing.BadSignature
                    source = signed['basis']
                except (signing.BadSignature,KeyError,TypeError,ValueError):
                    raise ValidationError('Распределение из факта устарело или изменено. Получите его заново либо выберите «Вручную».')
        group = state['groups'][gid]
        parameters = row.get('parameters',{})
        if not isinstance(parameters,dict) or any(key not in keys for key in parameters):
            raise ValidationError('Некорректные параметры мощностей.')
        for key,power in group['powers'].items():
            power['share'] = stored(parsed[key]) if key in parsed else '0'
            if key not in parameters:
                continue
            params = parameters[key]
            if not isinstance(params,dict):
                raise ValidationError('Некорректные параметры мощности.')
            factor,cost = number(params.get('factor'),optional=True),number(params.get('cost'),optional=True)
            if factor is not None and factor <= 0 or cost is not None and cost > HUNDRED:
                raise ValidationError('Соотношение DDP должно быть положительным, доля стоимости — от 0 до 100%.')
            power['factor'],power['indoor_cost_percent'] = stored(factor),stored(cost)
            for kind in ('indoor','outdoor'):
                model = params.get(kind) or None
                if model is not None and not any(item['id'] == model and item['kind'] == kind for item in power['models']):
                    raise ValidationError('Выбранная модель не входит в снимок этой мощности.')
                power[kind] = model
        group['power_distribution_mode'] = mode
        sources = group.setdefault('distribution_sources',{})
        if source: sources['powers'] = source
        else: sources.pop('powers',None)
    return state,len(rows)
