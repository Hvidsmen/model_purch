"""Build one candidate for sales and purchases; never persist partial results."""
import json
from copy import deepcopy
from django.core.exceptions import ValidationError
from django.utils import timezone
from .split_workflow import changed_state
from .split_group_plan import distribute
from .split_stock_workspace import presentation, supply_proposal
from .split_engine import stored


def candidate(scenario, post, calculate=False):
    fields = post.copy()
    fields['action'] = 'lines'
    state = changed_state(scenario, fields)
    try:
        targets = json.loads(post.get('pending_group_plans', '[]'))
    except (ValueError, TypeError):
        raise ValidationError('Некорректный список итоговых планов.')
    if not isinstance(targets, list) or any(not isinstance(gid, str) or gid not in state['groups'] for gid in targets) or len(set(targets)) != len(targets):
        raise ValidationError('Некорректный список итоговых планов.')
    for gid in targets:
        state = distribute(scenario, state, gid, post.get('group_plan_' + gid), post.get('group_plan_method_' + gid, 'saved'))
    draft = deepcopy(scenario)
    draft.state = state
    if calculate:
        ids = json.loads(post['plan_group_ids']) if 'plan_group_ids' in post else list(state['groups'])
        errors = []
        for row in presentation(draft)['rows']:
            if row['id'] not in ids: continue
            group = state['groups'][row['id']]
            try:
                group['buy'] = stored(supply_proposal(draft, group, row))
                group['buy_source'] = {'kind':'calculated','label':'По целевому запасу','at':timezone.now().isoformat()}
            except ValidationError as exc:
                errors.append(group['ref']['name'] + ': ' + ' '.join(exc.messages))
        if errors: raise ValidationError('Ничего не сохранено. ' + '; '.join(errors))
    return state


def report(scenario, state):
    draft = deepcopy(scenario)
    draft.state = state
    return {row['id']: {'planned':stored(row['planned']), 'closing':stored(row['closing']),
                       'target':stored(row['target']), 'status':row['statuses']['supplies']['message']}
            for row in presentation(draft)['rows']}
