"""Pack wide planning forms without exceeding Django's POST field limit."""
import json
from django.core.exceptions import ValidationError


def unpack(post):
    result=post.copy()
    if 'plan_payload' not in post:return result
    try:entries=json.loads(post['plan_payload'])
    except (ValueError,TypeError):raise ValidationError('Некорректный пакет полей плана. Обновите страницу и повторите сохранение.')
    if not isinstance(entries,list) or len(entries)>100000:
        raise ValidationError('Некорректный пакет полей плана.')
    reserved={'action','revision','csrfmiddlewaretoken','pending_group_plans','plan_payload'}
    for entry in entries:
        if not isinstance(entry,list) or len(entry)!=2 or not all(isinstance(value,str) for value in entry):
            raise ValidationError('Некорректное поле в пакете плана.')
        name,value=entry
        if len(name)>500:raise ValidationError('Некорректное имя поля плана.')
        if name not in reserved:result.appendlist(name,value)
    return result
