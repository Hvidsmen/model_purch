"""Display totals; do not change any plan inputs or persisted state."""
from decimal import Decimal
from .split_engine import precise


@precise
def aggregate(cells):
    cells = list(cells)
    result = {field: sum((cell[field] for cell in cells), Decimal(0))
              if cells and all(cell[field] is not None for cell in cells) else None
              for field in ('qty', 'revenue', 'cost')}
    result['markup'] = result['revenue'] / result['cost'] if result['revenue'] is not None and result['cost'] else None
    return result


def enrich_history(history):
    for group in history['groups']:
        for year in group['years']:
            year['total'] = aggregate(year['annual'])
            for month in year['months']:
                month['total'] = aggregate(month['cells'])
    combined = {'name': 'Все группы', 'years': []}
    for index, year_number in enumerate(history['years']):
        sources = [group['years'][index] for group in history['groups']]
        annual = []
        if sources:
            annual = [aggregate(source['annual'][i] for source in sources)
                      for i in range(len(sources[0]['annual']))]
        months = []
        for month_index in range(12):
            cells = [aggregate(source['months'][month_index]['cells'][i] for source in sources)
                     for i in range(len(annual))]
            months.append({'month': f'{year_number}-{month_index+1:02}', 'cells': cells, 'total': aggregate(cells)})
        combined['years'].append({'year': year_number, 'annual': annual, 'total': aggregate(annual), 'months': months})
    return combined


def plan_totals(data, state):
    cells = [{**row, 'qty': row['quantity']} for row in data['lines']]
    return {'groups': {gid: aggregate(row for row in cells if row['gid'] == gid) for gid in state['groups']},
            'channels': {cid: aggregate(row for row in cells if row['cid'] == cid) for cid in state['channels']},
            'total': aggregate(cells)}
