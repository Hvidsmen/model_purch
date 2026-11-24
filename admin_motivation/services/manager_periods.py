"""Dated manager corrections and their version templates in the same workspace."""
from django.utils import timezone
from base.access import role_for,ADMIN,DIRECTOR
from ..models import PeriodMotivationCoefficient,PremiumDistribution


def context(user,version,subdivision,managers,year=None):
    from .reports_next import period_code
    from .closing_coefficient import period_closed
    try:year=int(year)
    except (ValueError,TypeError):year=version.effective_from.year if version else timezone.localdate().year
    if not 2000<=year<=2100:year=timezone.localdate().year
    templates={period_code(row.kind.name):row.coeff for row in managers}
    confirmed={str(row.period):row for row in PeriodMotivationCoefficient.objects.filter(subdivision=subdivision,year=year)}
    locked=set(PremiumDistribution.objects.filter(subdivision=subdivision,year=year).values_list('period',flat=True))
    rows=[]
    for period in range(1,6):
        row=confirmed.get(str(period));template=templates.get(str(period),1)
        rows.append({'period':period,'label':f'{period} квартал {year}' if period<5 else f'Год {year}',
            'template':template,'record':row,'value':row.coefficient if row else template,'revision':row.revision if row else 0,'locked':period in locked,'closed':period_closed(year,period)})
    return {'manager_year':year,'manager_period_rows':rows,'can_confirm_manager_period':role_for(user,'motivation') in (ADMIN,DIRECTOR)}
