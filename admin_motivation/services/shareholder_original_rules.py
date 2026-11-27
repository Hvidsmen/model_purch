"""Versioned original payout rules for new immutable shareholder snapshots only."""
import re
from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql
from .reports_next import period_code

RULE = 'shareholder-original-2026-v2'
ZERO = Decimal(0)
DIMENSIONS = ('Подразделение', 'АвторСделки', 'Год', 'ГруппаПлановПродаж',
              'ГруппаТоваров', 'Марка', 'ТипКоэф', 'VariationCalculate')


def enabled(run):
    return run.configuration.get('rule') == RULE


def payout_relation(source, after):
    """Resolve the outer payout FROM, preserving a derived eligibility window."""
    from .pilot_formula import closing
    code=mask_sql(source);depth=0;start=None
    for token in re.finditer(r'\(|\)|\bFROM\b',code[after:],re.I):
        value=token[0].upper()
        if value=='(':depth+=1
        elif value==')':depth-=1
        elif depth==0:
            start=after+token.end();break
    if start is None:raise ValidationError('Не найден источник строк условия выплаты.')
    opening=re.match(r'\s*\(',code[start:])
    if opening:
        end=closing(code,start+opening.end()-1)
        alias=re.match(r'\s+(?:AS\s+)?(?P<name>\[?\w+\]?)',code[end+1:],re.I)
        if not alias:raise ValidationError('Не найден псевдоним источника условия выплаты.')
        return 'FROM'+source[start:end+1+alias.end()],alias['name']
    table=re.match(r'\s*#pf_base\b',code[start:],re.I)
    if not table:raise ValidationError('Неизвестный источник строк условия выплаты.')
    return 'FROM #pf_base','#pf_base'


def restore(source):
    """Undo just the transition's two Q4-only windows, before window evaluation."""
    source, count = re.subn(
        r'FROM\s+#pf_base\s+WHERE\s+(?:1\s*=\s*0\s+AND\s+)?\(Квартал\s*=\s*4\s+OR\s+@FactYear\s*<>\s*2026\)',
        'FROM #pf_base WHERE Квартал IN (1,2,3,4)', source, flags=re.I)
    if count != 2:
        raise ValidationError('Не распознаны два переходных окна IV квартала и года. Обновите задание «Все кварталы 2026».')
    # Read the installed annual USD eligibility predicate verbatim, including all
    # personal/global gates. Never infer eligibility from a positive annual amount:
    # the configured annual share may legitimately be zero.
    code = mask_sql(source)
    predicates = []; annual_input = None
    for match in re.finditer(r'\bIIF\s*\(', code, re.I):
        start = match.end(); depth = 1; parts = []; point = start
        for at in range(start, len(code)):
            char = code[at]
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    parts.append(source[point:at].strip())
                    break
            elif char == ',' and depth == 1:
                parts.append(source[point:at].strip()); point = at + 1
        if len(parts) == 3 and parts[1] == '0' and parts[2].strip('[]') == 'СуммаМотивацииUSDГод':
            predicates.append(parts[0])
            annual_input, qualifier = payout_relation(source, at+1)
            if qualifier=='#pf_base':annual_input+=' WHERE Квартал IN (1,2,3,4)'
    if len(predicates) != 1 or '@PayoutThreshold' not in predicates[0]+(annual_input or ''):
        raise ValidationError('Не распознано единственное годовое условие выплаты акционеров.')
    fields = ','.join('[' + field + ']' for field in DIMENSIONS)
    source += ('\nSELECT DISTINCT ' + fields + ',CASE WHEN (' + predicates[0] +
               ') THEN 0 ELSE 1 END AS YearEligible INTO #share_eligibility '
               + annual_input + ';\n')
    return source


def performance_sql(source):
    """Original cumulative windows, without changing ordinary report queries."""
    return source.replace('(pf.[Квартал]=4 OR pf.[Год]<>2026)', 'pf.[Квартал] IN (1,2,3,4)')


def identity(row, *, curator=False):
    values = [str(row.get(field) or '').strip() for field in DIMENSIONS]
    if not curator and values[0] == 'ТЕРМОТРЕЙД':
        values[0] = 'Д-Москва'
    return tuple(values)


def attach(rows, eligibility, *, curator=False):
    """Use this scenario's actual quarter payments; annual eligibility is frozen SQL."""
    rows = [dict(row) for row in rows]
    gates = {}
    for row in eligibility:
        key = identity(row, curator=curator)
        value = str(row.get('YearEligible'))
        if value not in ('0', '1', 'False', 'True'):
            raise ValidationError('Повреждён снимок годового условия выплаты.')
        eligible = value in ('1', 'True')
        if key in gates and gates[key] != eligible:
            raise ValidationError('Противоречивые годовые условия выплаты в снимке.')
        gates[key] = eligible
    gaps = defaultdict(lambda: [ZERO, ZERO])
    quarter_gaps = defaultdict(lambda: defaultdict(lambda: [ZERO, ZERO]))
    for row in rows:
        code = period_code(row.get('КварталМотивации'))
        if code not in ('1', '2', '3', '4'):
            continue
        key = identity(row, curator=curator)
        for index, (possible, paid, suffix) in enumerate((
                ('СуммаМотивацииUSD', 'СуммаМотивацииUSDУчетПроцента', 'USD'),
                ('СуммаМотивацииRUR', 'СуммаМотивацииRURУчетПроцента', 'RUB'))):
            value = max(Decimal(str(row.get(possible) or 0)) - Decimal(str(row.get(paid) or 0)), ZERO)
            row['PotentialCatchup' + suffix] = value
            gaps[key][index] += value
            quarter_gaps[key][code][index] += value
    seen = set()
    for row in rows:
        key = identity(row, curator=curator)
        annual = period_code(row.get('КварталМотивации')) == '5'
        row['_shareholder_original_rules'] = True
        row['Остаток'] = row['ОстатокRUR'] = ZERO
        if annual:
            if key not in gates:
                raise ValidationError('Нет сохранённого годового условия выплаты. Создайте новый расчёт акционеров.')
            if key in seen:
                raise ValidationError('Повтор годовой строки при расчёте доплат.')
            seen.add(key)
            for index, (suffix, remainder) in enumerate((('USD', 'Остаток'), ('RUB', 'ОстатокRUR'))):
                row['PotentialCatchup' + suffix] = gaps[key][index]
                row['CatchupQuarters' + suffix] = {code: values[index] for code, values in quarter_gaps[key].items()}
                row[remainder] = gaps[key][index] if gates[key] else ZERO
    if any(any(values) and key not in seen for key, values in gaps.items()):
        raise ValidationError('Нет годовой строки для невыплаченной квартальной премии.')
    return rows


def potential_total(rows):
    """Quarter gaps and their annual rollup must never be added together."""
    quarters = [row for row in rows if row['code'] != '5']
    return sum((row.get('potential_bonus', ZERO) for row in (quarters or rows)), ZERO)


def enrich(display, ledger):
    if not ledger.get('original_rules'):
        return
    display['potential_bonus'] = potential_total(ledger['rows'])
    for group in [*display['employees'], *display['registry_rows']]:
        group['potential_bonus'] = potential_total(group['rows'])
    for section in display['team_sections']:
        section['potential_bonus'] = potential_total([row for row in ledger['rows'] if row['kind'] == section['kind']])
    for card in display['period_cards']:
        card['potential_bonus'] = potential_total([row for row in ledger['rows'] if row['code'] == card['code']])
