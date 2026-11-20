"""Read-only workflow guidance, based on the existing validation result."""
from urllib.parse import urlencode
from decimal import Decimal

STAGES = [('composition', 'Состав плана', 'Выберите группы, каналы и способ задания объёма.'),
          ('plan', 'Продажи', 'Задайте объёмы продаж, DDP и наценки. Сравните результат с фактом.'),
          ('distribution', 'Распределение', 'Настройте месяцы для всех групп и каналов, мощности и пары моделей — для каждой группы.'),
          ('stock', 'Запасы и поставки', 'Проверьте обеспеченность плана товаром и добавьте новые поставки.'),
          ('checks', 'Проверка и утверждение', 'Исправьте ошибки, проверьте предупреждения и утвердите версию.')]


def destination(message):
    text = message.casefold()
    if any(word in text for word in ('остат', 'поступлен', 'дефицит', 'прогноз', 'закуп', 'постав')):
        return 'stock'
    if any(word in text for word in ('мощност', 'сезонност', 'месячн', 'пару блок', 'внутреннего блока')):
        return 'distribution'
    if 'справочник' in text:
        return 'composition'
    return 'plan'


def presentation(scenario, result, tab):
    issues = []
    for severity, messages in [('error', result['errors']), ('warning', result['warnings'])]:
        for message in messages:
            target = destination(message)
            group = next(((gid,g) for gid,g in sorted(scenario.state['groups'].items(), key=lambda item: -len(item[1]['ref']['name']))
                          if message.startswith(g['ref']['name'] + ':') or message.startswith(g['ref']['name'] + ' · ')), None)
            query = {'tab':target}
            if group:
                query.update(group=group[0], focus='1')
                power = next((key for key in group[1]['powers'] if ' · '+key+':' in message), None)
                if power is not None:
                    query['power'] = power
                if target == 'distribution':
                    query['section'] = 'powers' if any(word in message.casefold() for word in ('мощност','пару блок','внутреннего блока')) else 'months'
            issues.append({'message':message,'severity':severity,'tab':target,'url':'?' + urlencode(query),'action':'Перейти к исправлению' if severity == 'error' else 'Посмотреть'})
    has_composition = bool(scenario.state['groups'] and scenario.state['channels'])
    stages = []
    for index, (key,title,description) in enumerate(STAGES, 1):
        errors = [issue for issue in issues if issue['severity']=='error' and (key=='checks' or issue['tab']==key)]
        warnings = [issue for issue in issues if issue['severity']=='warning' and (key=='checks' or issue['tab']==key)]
        if key == 'composition':
            detail = f"Групп: {len(scenario.state['groups'])} · каналов: {len(scenario.state['channels'])}"
        elif errors:
            detail = f'Нужно исправить: {len(errors)}'
        elif warnings:
            detail = f'Предупреждений: {len(warnings)}'
        else:
            detail = 'По сохранённым данным'
        status = 'Не начат' if not has_composition else 'Требует внимания' if errors or warnings else 'Заполнен'
        if key == 'composition' and has_composition and not errors:
            status = 'Заполнен'
        if key == 'stock' and not any(line.get('quantity') for line in result.get('lines', [])):
            status, detail = 'Не начат', 'Сначала задайте положительный план продаж'
        if key == 'plan' and has_composition:
            filled=sum(all(line.get('input') is not None for line in group['channels'].values()) and (gid in scenario.state.get('sales_reviewed_groups',[]) or any(Decimal(str(line.get('input') or 0))!=0 for line in group['channels'].values())) for gid,group in scenario.state['groups'].items())
            positive=sum(any(line.get('quantity') for line in result.get('lines',[]) if line['gid']==gid) for gid in scenario.state['groups'])
            detail=f"План задан: {filled} из {len(scenario.state['groups'])} групп · с продажами: {positive}"
            status='Требует внимания' if errors or warnings else 'Не завершён' if filled<len(scenario.state['groups']) else 'План задан'
        if key == 'distribution' and has_composition:
            from .split_distribution_overview import overview
            month_ready=overview(scenario,'months',{});power_ready=overview(scenario,'powers',{})
            detail=f"Месяцы: {month_ready['counts']['ready']} из {month_ready['required_total']} · мощности: {power_ready['counts']['ready']} из {power_ready['required_total']}"
            if any(report['counts']['ready']<report['required_total'] for report in (month_ready,power_ready)):status='Не завершён'
        if key == 'checks' and scenario.status == 'approved':
            status = 'Утверждён'
        stages.append({'key':key,'title':title,'description':description,'number':index,'status':status,'detail':detail,'current':tab==key})
    return {'stages':stages,'current':next((stage for stage in stages if stage['key']==tab), None),
            'errors':[issue for issue in issues if issue['severity']=='error'],
            'warnings':[issue for issue in issues if issue['severity']=='warning']}
