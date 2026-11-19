"""Frozen currencies for private coefficient-iteration results."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from .sales_plans import number

RULE='iteration-currency-v1-deal-rub'
ARRAYS=('quarters','possible','annual_quarters')
SCALARS=('annual_accrued','annual_paid','bonus','total')


def configuration(values):
    result=dict(values)
    for year in (2026,2027):
        field=f'usd_rub_{year}'
        rate=number(result.get(field) if result.get(field) is not None else 80)
        if not Decimal(0)<rate<=Decimal('1000000'):
            raise ValidationError(f'Курс USD/RUB для {year} должен быть больше нуля и не больше 1 000 000.')
        result[field]=str(rate)
    result['currency_rule']=RULE
    return result


def money(row):
    return {field:row[field] for field in (*ARRAYS,*SCALARS)}


def convert_money(row,rate):
    rate=number(rate)
    return {**{field:[number(value)*rate for value in row[field]] for field in ARRAYS},
            **{field:number(row[field])*rate for field in SCALARS}}


def display_rows(rows,currency='USD'):
    if currency=='USD':return rows
    if any(not isinstance(row.get('rub'),dict) or any(field not in row['rub'] for field in (*ARRAYS,*SCALARS)) for row in rows):
        return None  # Never invent historical RUB values for an old snapshot.
    return [{**row,**money(row['rub']),**({'allocated_plan':row.get('allocated_plan_rub')} if 'allocated_plan' in row else {})} for row in rows]


def convert_bases(rows,rate):
    rate=number(rate)
    result=[]
    for row in rows:
        item=dict(row)
        for field in ('plan','fact'):
            item[field]=number(row[field])*rate if row.get(field) is not None else None
        item['segments']=[number(value)*rate for value in row['segments']] if row.get('segments') is not None else None
        result.append(item)
    return result
