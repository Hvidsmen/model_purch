"""Closed-deal presentation from immutable calculation facts, never live DWH."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from .deal_filter_choices import COLUMNS as FILTER_COLUMNS
from .deal_presentation import COLUMNS, prepare
from .reports import FILTERS, parameters
from .shareholder_reports import KINDS

VERSION = 'closed-deals-v1'


def snapshot_sql(kind):
    # Fact contains actual shipments, not the synthetic plan/payout rows in Report.
    from .motivation_exclusions import sql_allowed
    from .subdivision_scope import sql_predicate
    fields = ','.join('[' + key + ']' for key, _, _ in COLUMNS)
    return ('SELECT ' + fields + ',[ТипКоэф] FROM #share_fact '
            "WHERE LTRIM(RTRIM([ТипКоэф]))=N'" + kind + "' AND "
            + sql_predicate('[Подразделение]') + ' AND ' + sql_allowed(kind))


def records(run, kind):
    from .shareholder_regions import enabled, REGIONS
    regional = enabled(run)
    chunks = run.chunks.filter(dataset='deals_' + KINDS[kind])
    if run.scenario == 'combined' or kind == 'Кураторы':
        chunks = chunks.filter(mode='combined')
    for chunk in chunks.only('mode', 'payload').order_by('mode', 'sequence').iterator(chunk_size=16):
        for stored in chunk.payload:
            row = dict(stored)
            closed = date.fromisoformat(str(row['ДатаЗакрытияСделки'])[:10])
            if closed > run.as_of or closed.year != 2026 or (closed.month-1)//3+1 not in run.quarters:
                continue
            mode = 'separate' if run.scenario == 'separate' and closed.month < 10 else 'combined'
            if kind == 'Кураторы' or regional and row.get('Подразделение') in REGIONS:
                mode = 'combined'
            if chunk.mode != mode:
                continue
            row['ДатаЗакрытияСделки'] = closed
            if row.get('ДатаОтгрузки'):
                row['ДатаОтгрузки'] = date.fromisoformat(str(row['ДатаОтгрузки'])[:10])
            for key, _, type_ in COLUMNS:
                if type_ in ('money', 'percent', 'raw_percent') and row.get(key) is not None:
                    row[key] = Decimal(str(row[key]))
            yield row


def build(run, query, *, load=True):
    values = parameters('deals', query)
    kind = query.get('type')
    if kind and kind not in KINDS:
        raise ValidationError('Недоступный вид мотивации.')
    selected = {key: query.getlist(key) for key in FILTER_COLUMNS}
    options = {key: set() for key in FILTER_COLUMNS}
    options['type'].update(KINDS)
    matched = []
    for source_kind in ([kind] if kind else KINDS):
        for row in records(run, source_kind):
            if not values['StartDate'] <= row['ДатаЗакрытияСделки'] <= values['EndDate']:
                continue
            failed = {key for key, column in FILTER_COLUMNS.items()
                      if key != 'type' and selected[key] and row.get(column) not in selected[key]}
            # Primary selectors are independent of previous product/author choices.
            options['subdivision'].add(row.get('Подразделение'))
            if kind and query.get('subdivision'):
                for key, column in FILTER_COLUMNS.items():
                    if key not in ('type', 'subdivision') and not (failed - {key}):
                        options[key].add(row.get(column))
                if not failed and load:
                    matched.append(row)
    filters = []
    for name, _, label, _, multiple in FILTERS['deals']:
        choices = options[name] | set(selected[name])
        filters.append(dict(name=name, label=label, multiple=multiple,
                            options=[dict(value=v, selected=v in selected[name]) for v in sorted(v for v in choices if v)]))
    if not load or not kind or not query.get('subdivision'):
        return None, filters
    matched.sort(key=lambda r: (-r['ДатаЗакрытияСделки'].toordinal(), str(r.get('Номер') or ''),
                                str(r.get('Модель') or ''), str(r.get('АвторСделки') or '')))
    amounts = {'shipments': 'СуммаОтгрузокUSD', 'quarterly': 'СуммаКвМотивацииUSD', 'annual': 'СуммаМотивацииUSDГод'}
    authors = defaultdict(lambda: dict(count=0, shipments=Decimal(0), quarterly=Decimal(0), annual=Decimal(0)))
    for row in matched:
        total = authors[row.get('АвторСделки') or '—']; total['count'] += 1
        for key, column in amounts.items():
            total[key] += row.get(column) or Decimal(0)
    summary = [dict(author=author, **total, possible=total['quarterly']+total['annual']) for author, total in sorted(authors.items())]
    totals = {key: sum((r[key] for r in summary), Decimal(0)) for key in ('count', *amounts, 'possible')}
    page = Paginator(matched, 50).get_page(query.get('page', 1))
    data = dict(summary=summary, totals=totals, details=list(page), page=page.number,
                pages=page.paginator.num_pages, offset=(page.number-1)*50)
    return prepare(data, query), filters
