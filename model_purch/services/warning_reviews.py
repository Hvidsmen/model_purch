"""Reviews bind to exact source values, not rounded warning text."""
import hashlib
import json
from .plan_validation import plan_warnings, duty_warnings
from ..goods_identity import planning_group_key

FIELDS = {
    'ddp': ('ddp_usd', 'plan_ddp_usd'),
    'volume': ('volume', 'plan_volume'),
    'fact_ddp': ('ddp_usd', 'fact_ddp_usd'),
    'fact_volume': ('volume', 'fact_volume'),
    'missing': ('plan_ddp_usd', 'plan_volume'),
    'duty_small': ('duty_rate',),
    'duty_group': ('duty_rate', 'group_goods'),
}


def warning_details(good, threshold, duties, reviews=None):
    result = []
    for kind, message in plan_warnings(good, threshold) + duty_warnings(good, duties):
        values = {field: (getattr(good, field) if field == 'group_goods' or getattr(good, field) is None
                          else float(getattr(good, field))) for field in FIELDS[kind]}
        if kind == 'duty_group':
            group_rate = duties.get(planning_group_key(good.group_goods))
            values['group_rate'] = None if group_rate is None else float(group_rate)
        if kind in ('ddp', 'volume', 'fact_ddp', 'fact_volume'):
            values['threshold'] = float(threshold)
        token = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()
        review = (reviews or {}).get(kind)
        result.append({'kind': kind, 'message': message, 'token': token,
                       'reviewed': bool(review and review.fingerprint == token), 'review': review})
    return result
