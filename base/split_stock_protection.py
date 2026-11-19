"""Groups with known stock must remain in scenario composition."""
from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime
from .split_engine import number, ZERO


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


def validate_removal(state, gids):
    locked = protected_groups(state)
    blocked = [name for gid, name in locked.items() if gid in gids]
    if blocked:
        raise ValidationError('Нельзя удалить группы, по которым есть остатки на стоке: ' + ', '.join(sorted(blocked)) + '.')
