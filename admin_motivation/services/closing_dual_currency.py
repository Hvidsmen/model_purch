"""Show actual currency amounts, preserving the provenance of frozen funds."""
from decimal import Decimal
from .premium_distribution import cash

KINDS=(('Продажи','sales'),('Политики','policies'),('Директора','directors'))


def totals(funds,kind):
    members=[f for f in funds if f['kind']==kind]
    return sum((cash(f['amount']) for f in members),Decimal(0)) if members else None


def funds(batch, comparisons, execution, record):
    calculated={currency:[{'kind':r['kind'],'amount':str(r['after'])} for r in value['kinds']] for currency,value in comparisons.items()}
    if batch is None:
        sources=calculated
    else:
        sources=dict(batch.source.get('funds_by_currency',{}))
        sources[batch.currency]=batch.funds
        same_run=batch.execution_id==execution.pk
        same_coefficient=bool(record and batch.source.get('workflow',{}).get('coefficient_revision')==record.revision)
        same_funds=all(totals(batch.funds,kind)==totals(calculated[batch.currency],kind) for kind,_ in KINDS if totals(batch.funds,kind) is not None)
        if batch.state in ('draft','completed') and same_run and same_coefficient and same_funds:
            for currency in ('USD','RUB'):
                sources.setdefault(currency,[f for f in calculated[currency] if any(old['kind']==f['kind'] for old in batch.funds)])
    return {prefix+'_'+currency.lower():totals(sources[currency],kind) if currency in sources else None
            for kind,prefix in KINDS for currency in ('USD','RUB')}
