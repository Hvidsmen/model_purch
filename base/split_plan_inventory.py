"""Project the stock tab's existing balances into the plan's all-channel totals."""
def presentation(stock):
    groups = {}
    for row in stock['groups']:
        groups[row['id']] = {
            'stock': row['stock_sets'], 'prior': row['prior_sets'], 'forecast': row['opening']['forecast_total'],
            'opening': row['opening']['totals']['sets'], 'existing': row['incoming_sets'],
            'new': row['new_supply'], 'closing': row['closing'],
            'powers': {power['key']: {
                'stock': power['stock_sets'], 'prior': power['prior_sets'], 'forecast': power['opening']['forecast'],
                'opening': power['opening']['sets'], 'existing': power['incoming_sets'],
                'new': power['new_supply'], 'closing': (power.get('ending') or {}).get('closing'),
            } for power in row['powers']},
        }
    totals = stock['totals']
    return {'groups': groups, 'total': {
        'stock': totals['stock_sets'], 'prior': totals['prior_sets'], 'forecast': totals['forecast'],
        'opening': totals['sets'], 'existing': totals['incoming_sets'], 'new': totals['new_supply'], 'closing': totals['closing'],
    }}
