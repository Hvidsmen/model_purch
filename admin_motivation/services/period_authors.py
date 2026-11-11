"""Restrict a report's author cohort to physical fact rows in its premium period."""
from django.core.exceptions import ValidationError

TABLES={'main':'DataWH.motivation.Fact','all_2026':'DataWH.motivation.FactAll2026',
        'separate_2026':'DataWH.motivation.FactSeparate2026'}


def quarters(periods,variant='main'):
    if variant not in TABLES:raise ValidationError('Неизвестный источник авторов периода.')
    codes=list(periods) if periods else (['4','5'] if variant=='main' else ['1','2','3'] if variant=='separate_2026' else ['1','2','3','4','5'])
    if any(code not in ('1','2','3','4','5') for code in codes):raise ValidationError('Неизвестный период авторов.')
    # These report namespaces use 2026 annual accrual from Q4 only. Q2's
    # cumulative eligibility check does not turn its author cohort into Q1+Q2.
    selected={int(code) for code in codes if code!='5'}
    if '5' in codes:selected.add(4)
    if variant=='main':selected&={4}
    if variant=='separate_2026':selected&={1,2,3}
    return sorted(selected)


def restrict_sql(source,periods=None,variant='main'):
    selected=quarters(periods,variant)
    if not selected:return 'SELECT * FROM ('+source+') AS period_author_report WHERE 1=0'
    return f"""SELECT period_author_report.* FROM ({source}) AS period_author_report
WHERE EXISTS (
 SELECT 1 FROM {TABLES[variant]} AS activity
 WHERE activity.[Подразделение]=period_author_report.[Подразделение]
 AND LTRIM(RTRIM(COALESCE(activity.[АвторСделки],N'')))=LTRIM(RTRIM(COALESCE(period_author_report.[АвторСделки],N'')))
 AND activity.[ТипКоэф]=period_author_report.[ТипКоэф]
 AND activity.[Год]=period_author_report.[Год]
 AND activity.[Квартал] IN ({','.join(str(q) for q in selected)})
 AND activity.[ГруппаПлановПродаж] IN (@gp_sales)
 AND activity.[ГруппаТоваров] IN (@GPGoods)
 AND activity.[Марка] IN (@Brend)
)"""
