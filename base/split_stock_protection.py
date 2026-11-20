"""Require explicit acknowledgement before archiving groups with stock."""
import json
from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime
from .split_engine import number, ZERO, precise


def protected_groups(state):
    selected = {str(row['id']) for row in state.get('inventory_selection', {}).get('groups', []) if row.get('stock')}
    selected_at = parse_datetime(state.get('inventory_selection', {}).get('checked_at', '') or '')
    protected = {}
    for gid, group in state['groups'].items():
        source = group.get('inventory_source') or {}
        # SQL stock rows contain positive quantities; support older import metadata.
        imported = source.get('has_stock', bool(source.get('stock_models', 0)))
        physical = any((number(power.get('stock_' + kind), optional=True) or ZERO) > ZERO
                       for power in group['powers'].values() for kind in ('indoor', 'outdoor'))
        selected_presence = gid in selected
        loaded_at = parse_datetime(source.get('loaded_at', '') or '')
        if loaded_at and (not selected_at or loaded_at.tzinfo == selected_at.tzinfo and loaded_at >= selected_at):
            selected_presence = False
        if physical or imported or selected_presence:
            protected[gid] = group['ref']['name']
    return protected


@precise
def snapshot_stock(group):
    """Physical sets at the stock snapshot date, before any future movements."""
    pairs = [(number(power.get('stock_indoor'),optional=True),number(power.get('stock_outdoor'),optional=True))
             for power in group['powers'].values()]
    known = bool(pairs) and all(indoor is not None and outdoor is not None for indoor,outdoor in pairs)
    return {'quantity':sum((min(indoor,outdoor) for indoor,outdoor in pairs),ZERO) if known else None,
            'has_stock':any(value is not None and value > ZERO for pair in pairs for value in pair)}


def removal_groups(scenario, gids=None):
    ids = set(scenario.state['groups']) if gids is None else set(gids)
    return {gid:name for gid,name in protected_groups(scenario.state).items() if gid in ids}


def validate_removal(scenario, gids, confirmation='[]'):
    required = removal_groups(scenario,gids)
    if not required: return
    try: acknowledged = json.loads(confirmation)
    except (ValueError,TypeError): acknowledged = None
    if not isinstance(acknowledged,list) or any(not isinstance(gid,str) for gid in acknowledged) or not set(required) <= set(acknowledged):
        raise ValidationError('Вы исключаете группы с остатками: ' + ', '.join(sorted(required.values())) +
                              '. Подтвердите исключение перед сохранением. Данные групп будут сохранены в архиве черновика.')
