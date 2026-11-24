"""Reference implementation for the reviewed proportional allocation rule."""
from collections import defaultdict
from decimal import Decimal,localcontext
from django.core.exceptions import ValidationError


def author_weights(turnovers,threshold_percent):
    threshold=Decimal(str(threshold_percent))/100
    if not threshold.is_finite() or not 0<=threshold<1:raise ValidationError('Некорректный порог авторов.')
    branches=defaultdict(dict)
    for branch,author,amount in turnovers:
        value=Decimal(str(amount))
        if not value.is_finite():raise ValidationError('Некорректный оборот.')
        name=str(author or '').strip()
        branches[branch][name]=branches[branch].get(name,Decimal(0))+value
    result=[]
    for branch,authors in branches.items():
        total=sum(authors.values(),Decimal(0))
        if total<=0:
            if any(authors.values()):raise ValidationError('Нет положительного оборота для определения долей: '+str(branch))
            continue
        recipients={name:value for name,value in authors.items() if name and value>0 and value/total>=threshold}
        if not recipients:raise ValidationError('Все авторы ниже порога: уменьшите порог для '+str(branch))
        ordered=sorted(recipients,key=lambda name:(-recipients[name],name))
        with localcontext() as context:
            context.prec=38
            denominator=sum(recipients.values(),Decimal(0))
            shares={name:recipients[name]/denominator for name in ordered[1:]}
            shares[ordered[0]]=Decimal(1)-sum(shares.values(),Decimal(0))
        for original in sorted(authors):
            for assigned,weight in ([(original,Decimal(1))] if original in recipients else [(name,shares[name]) for name in ordered]):
                result.append({'branch':branch,'original':original,'assigned':assigned,'weight':weight})
    return result


def split_amount(amount,assignments):
    """The first recipient receives the exact residual, including for returns."""
    amount=Decimal(str(amount));values=[amount*row['weight'] for row in assignments]
    if values:values[0]=amount-sum(values[1:],Decimal(0))
    return values
