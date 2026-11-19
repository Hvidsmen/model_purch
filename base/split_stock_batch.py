"""Apply edited stock rows to a private draft, then persist once."""
from copy import deepcopy
import json
from django.core.exceptions import ValidationError
from django.http import QueryDict
from .split_workflow import changed_state


def apply(scenario, raw):
    if scenario.status != 'draft' or len(raw) > 2_000_000:
        raise ValidationError('Нельзя сохранить этот набор изменений.')
    try:
        rows = json.loads(raw)
    except (ValueError, TypeError):
        raise ValidationError('Некорректный набор изменений.')
    if not isinstance(rows, list) or not rows or len(rows) > len(scenario.state['groups']):
        raise ValidationError('Выберите изменённые группы.')
    draft, seen = deepcopy(scenario), set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('gid'), str) or row['gid'] not in draft.state['groups'] or row['gid'] in seen:
            raise ValidationError('Группа отсутствует в плане или повторяется.')
        entries = row.get('entries')
        if not isinstance(entries, list) or any(not isinstance(pair,list) or len(pair)!=2 or not all(isinstance(value,str) for value in pair) for pair in entries):
            raise ValidationError('Некорректные поля группы.')
        seen.add(row['gid'])
        post = QueryDict('', mutable=True)
        for name, value in entries:
            post[name] = value
        post['action'], post['group'] = 'stock', row['gid']
        draft.state = changed_state(draft, post)
    return draft.state, len(seen)
