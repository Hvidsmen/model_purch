"""Capture the original per-product, per-kind rates from a validated DWH run."""
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from ..models import FaoDocument


def lookup(branch,number,document_date):
    number=str(number).strip()
    if not number or len(number)>255 or number.startswith('ФАО#'):raise ValidationError('Укажите номер документа.')
    existing=FaoDocument.objects.filter(subdivision=branch,number=number,document_date=document_date).first()
    if existing:return existing
    from ..premium_distribution_views import latest_calculation
    from .execution_tracking import latest_for_report
    from .reports_next import current_run
    from ..conns import connect_database
    execution=latest_for_report(latest_calculation('main'),variant='main',persist=True)
    if not execution or execution.state!='completed':raise ValidationError('Нужен завершённый основной расчёт для поиска ОЗС.')
    with current_run(execution):
        connection,cursor=connect_database('vm-dwh','DataWH')
        try:
            connection.timeout=30
            cursor.execute("""SELECT TOP (5001) * FROM DataWH.motivation.Fact
             WHERE [Номер]=? AND CAST([ДатаОтгрузки] AS date)=?
             AND ([ПодразделениеДокумент]=? OR [ПодразделениеДокумент] IN
              (SELECT SubdivisionKey FROM DataWH.dbo.Subdivisions WHERE SubdivisionName=?))
             AND LTRIM(RTRIM([ТипКоэф])) IN (N'Политики',N'Продажи',N'Директора')""",number,document_date,branch.subdivision_key,branch.subdivision_key)
            names=[column[0] for column in cursor.description]
            rows=[dict(zip(names,row)) for row in cursor.fetchall()]
        finally:connection.close()
    if not rows:raise ValidationError('Документ с этим номером и датой не найден в подразделении. Дата — дата отгрузки исходной ОЗС.')
    if len(rows)>5000:raise ValidationError('Слишком много строк документа. Уточните исходные данные.')
    rows=json.loads(json.dumps(rows,cls=DjangoJSONEncoder,ensure_ascii=False))
    if any(row.get('СуммаМотивацииUSD') is None or row.get('СуммаОтгрузокUSD') is None or row.get('ДатаЗакрытияСделки') is None for row in rows):
        raise ValidationError('В исходной ОЗС нет полной суммы, даты закрытия или расчётной премии. Нужен корректный исходный расчёт.')
    kinds={str(row['ТипКоэф']).strip() for row in rows}
    base_kind='Продажи' if 'Продажи' in kinds else 'Политики' if 'Политики' in kinds else 'Директора'
    for row in rows:row['ТипКоэф']=str(row['ТипКоэф']).strip()
    if len({str(row.get('Партнер') or '') for row in rows if row['ТипКоэф']==base_kind})>1:
        raise ValidationError('Номер и дата соответствуют нескольким партнёрам. Нужна сверка идентификатора документа; строки не объединены.')
    amount=sum((Decimal(str(row['СуммаОтгрузокUSD'])) for row in rows if row['ТипКоэф']==base_kind),Decimal(0)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    digest=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    document,_=FaoDocument.objects.get_or_create(subdivision=branch,number=number,document_date=document_date,
        defaults=dict(execution=execution,rows=rows,original_amount=amount,source_hash=digest))
    return document


def summary(document):
    rows=document.rows
    return dict(document=document,partners=sorted({str(r.get('Партнер') or '—') for r in rows}),
        authors=sorted({str(r.get('АвторСделки') or '—') for r in rows}),
        close_dates=sorted({str(r.get('ДатаЗакрытияСделки') or '')[:10] for r in rows}),
        premiums=[dict(kind=kind,amount=sum((Decimal(str(r.get('СуммаМотивацииUSD') or 0)) for r in rows if r['ТипКоэф']==kind),Decimal(0))) for kind in sorted({r['ТипКоэф'] for r in rows})])
