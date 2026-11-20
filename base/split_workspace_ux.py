"""Read-only workflow guidance, based on the existing validation result."""
from urllib.parse import urlencode
from decimal import Decimal

STAGES = [
    ('composition', 'Состав плана', 'Выберите группы, каналы и связи групп.'),
    ('months', 'Сезонность', 'Распределите продажи по месяцам для всех групп и каналов.'),
    ('powers', 'Мощности и модели', 'Задайте доли мощностей и пары моделей.'),
    ('opening', 'Начальный запас', 'Загрузите остатки и поступления, рассчитайте прогноз до начала плана.'),
    ('targets', 'Стоковая политика', 'Задайте целевой запас в процентах от плана.'),
    ('plan', 'Продажи и закупки', 'Сбалансируйте продажи, новые закупки и остаток на конец. Проверьте и утвердите план.'),
]
ROUTES = {'composition': '?tab=composition', 'months': '?tab=distribution&section=months',
          'powers': '?tab=distribution&section=powers', 'opening': '?tab=stock&stock_step=opening',
          'targets': '?tab=stock&stock_step=targets', 'plan': '?tab=plan'}


def stage_key(tab, section='months', stock_step='opening'):
    if tab == 'distribution': return 'powers' if section == 'powers' else 'months'
    if tab == 'stock': return 'targets' if stock_step == 'targets' else 'plan' if stock_step == 'supplies' else 'opening'
    return 'plan' if tab == 'checks' else tab


def destination(message):
    text = message.casefold()
    if any(word in text for word in ('остат', 'поступлен', 'дефицит', 'прогноз', 'закуп', 'постав')):
        return 'stock'
    if any(word in text for word in ('мощност', 'сезонност', 'месячн', 'пару блок', 'внутреннего блока')):
        return 'distribution'
    if 'справочник' in text:
        return 'composition'
    return 'plan'


def presentation(scenario, result, tab, section='months', stock_step='opening'):
    current_key = stage_key(tab, section, stock_step)
    issues = []
    for severity, messages in [('error', result['errors']), ('warning', result['warnings'])]:
        for message in messages:
            target = destination(message)
            target_section = 'powers' if any(word in message.casefold() for word in ('мощност','пару блок','внутреннего блока')) else 'months'
            target_step = 'opening' if any(word in message.casefold() for word in ('прогноз','начал')) else 'supplies'
            if 'целев' in message.casefold() and 'процент' in message.casefold():
                target, target_step = 'stock', 'targets'
            query = {'tab': 'plan' if target == 'stock' and target_step == 'supplies' else target}
            if target == 'distribution': query['section'] = target_section
            if target == 'stock' and target_step in ('opening','targets'): query['stock_step'] = target_step
            group = next(((gid,g) for gid,g in sorted(scenario.state['groups'].items(), key=lambda item: -len(item[1]['ref']['name']))
                          if message.startswith(g['ref']['name'] + ':') or message.startswith(g['ref']['name'] + ' · ')), None)
            if group:
                query.update(group=group[0], focus='1')
                power = next((key for key in group[1]['powers'] if ' · '+key+':' in message), None)
                if power is not None: query['power'] = power
            issues.append({'message':message,'severity':severity,'tab':target,'stage':stage_key(target,target_section,target_step),
                           'url':'?' + urlencode(query),'action':'Перейти к исправлению' if severity == 'error' else 'Посмотреть'})
    from .split_distribution_overview import overview
    from .split_stock_workspace import presentation as stock_presentation
    stock = stock_presentation(scenario)
    has_composition = bool(scenario.state['groups'] and scenario.state['channels'])
    stages = []
    for index, (key,title,description) in enumerate(STAGES, 1):
        errors = [issue for issue in issues if issue['severity']=='error' and issue['stage']==key]
        warnings = [issue for issue in issues if issue['severity']=='warning' and issue['stage']==key]
        total = len(scenario.state['groups'])
        ready = 0
        if key == 'composition':
            ready = total if has_composition else 0
            detail = f"Групп: {total} · каналов: {len(scenario.state['channels'])}"
        elif key in ('months','powers'):
            report = overview(scenario,key,{})
            ready,total = report['counts']['ready'],report['required_total']
            detail = f"Заполнено: {ready} из {total}"
        elif key in ('opening','targets'):
            counts = stock['counts'][key]
            ready = counts['ready']
            detail = f"Готово: {ready} из {total} групп"
        else:
            ready = sum(all(line.get('input') is not None for line in group['channels'].values()) and
                        (gid in scenario.state.get('sales_reviewed_groups',[]) or any(Decimal(str(line.get('input') or 0)) != 0 for line in group['channels'].values()))
                        for gid,group in scenario.state['groups'].items())
            detail = f"Продажи: {ready} из {total} · баланс: {stock['counts']['supplies']['ready']} из {total}"
            if result['errors']: detail += f" · ошибок: {len(result['errors'])}"
        status = 'Не начат' if not has_composition or not ready else 'Заполнено частично' if ready < total else 'Готов'
        if errors or warnings: status = 'Требует внимания'
        if key == 'plan' and (result['errors'] or stock['counts']['supplies']['ready'] < total): status = 'Требует внимания' if ready else 'Не начат'
        if key == 'plan' and scenario.status == 'approved': status = 'Утверждён'
        stages.append({'key':key,'url':ROUTES[key],'title':title,'description':description,'number':index,
                       'status':status,'detail':detail,'current':current_key==key})
    current = next((stage for stage in stages if stage['key']==current_key), None)
    next_stage = stages[current['number']] if current and current['number'] < len(stages) else None
    return {'stages':stages,'current':current,'next':next_stage,
            'errors':[issue for issue in issues if issue['severity']=='error'],
            'warnings':[issue for issue in issues if issue['severity']=='warning']}
