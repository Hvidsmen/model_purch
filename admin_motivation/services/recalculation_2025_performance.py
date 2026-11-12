"""Explain saved 2025 payouts using the same run's eligibility inputs."""
from collections import defaultdict
from decimal import Decimal
from .recalculation_2025 import decimal
from .subdivision_scope import EXCLUDED,sql_predicate
from .heat_exclusion import predicate

# Projected plan/fact are already window totals: never SUM them across goods rows.
# Q1's two inputs are normalized to physical shipments; no threshold is applied.
DETAIL_SQL = """
SELECT LTRIM(RTRIM([ТипКоэф])),[Подразделение],[АвторСделки],
 [ГруппаПлановПродаж],[ГруппаТоваров],[Марка],[VariationCalculate],[КварталМотивации],
 CASE WHEN [КварталМотивации]=N'1 Квартал' THEN SUM([СуммаОтгрузокUSDКварталValue])
 ELSE COALESCE(MAX(NULLIF([СуммаПланUSD],0)),0) END,
 CASE WHEN [КварталМотивации]=N'1 Квартал' THEN SUM([СуммаОтгрузокUSDКварталValue])
 ELSE COALESCE(MAX(NULLIF([СуммаОтгрузокUSD],0)),0) END,
 SUM(COALESCE([СуммаМотивацииUSD],0)),
 SUM(COALESCE([СуммаМотивацииUSDУчетПроцента],0)),SUM(COALESCE([Остаток],0))
FROM motivation.ReportMotivationByDealAuthorTest2025
WHERE [Год]=2025 AND LTRIM(RTRIM([ТипКоэф])) IN (N'Политики',N'Продажи')
GROUP BY [ТипКоэф],[Подразделение],[АвторСделки],[ГруппаПлановПродаж],
 [ГруппаТоваров],[Марка],[VariationCalculate],[КварталМотивации]
ORDER BY [ТипКоэф],[Подразделение],[АвторСделки],[ГруппаПлановПродаж],
 [ГруппаТоваров],[Марка],[КварталМотивации]
"""


def gates_sql():
    """Reproduce auxiliary author/department gates after the #pf_base grouping.

    Legacy personal formulae substitute 1 for a zero plan per grouped input row;
    global department mode uses the genuine plan sum. Keep both denominators.
    """
    scope = sql_predicate('[Подразделение]')
    return """
WITH base AS (
 SELECT LTRIM(RTRIM([ТипКоэф])) AS Kind_,[Подразделение],[АвторСделки],[Квартал],
 SUM([СуммаПланUSD]) AS Plan_,SUM([СуммаОтгрузокUSD]) AS Fact_
 FROM motivation.PlanFactTest2025
 WHERE [Год]=2025 AND LTRIM(RTRIM([ТипКоэф])) IN (N'Политики',N'Продажи')
 AND """ + scope + """
 AND (LTRIM(RTRIM([ТипКоэф]))<>N'Политики' OR
 (([Канал] IS NULL OR [Канал]<>N'ДПП') AND NOT (""" + predicate('[ГруппаПлановПродаж]') + """)))
 GROUP BY [ТипКоэф],[Подразделение],[ПодразделениеДокумент],[Канал],[Год],[Квартал],
 [ГруппаПлановПродаж],[ГруппаТоваров],[Марка],[АвторСделки],K0,K1,K2,K3,K4,[VariationCalculate])
SELECT Kind_,[Подразделение],[АвторСделки],p.Period_,SUM(Plan_),
 SUM(CASE WHEN Plan_=0 THEN 1 ELSE Plan_ END),SUM(Fact_)
FROM base CROSS JOIN (VALUES(1),(2),(3),(4),(5)) p(Period_)
WHERE (p.Period_=1 AND [Квартал]=1) OR (p.Period_=2 AND [Квартал] IN (1,2))
 OR (p.Period_=3 AND [Квартал]=3) OR p.Period_ IN (4,5)
GROUP BY Kind_,[Подразделение],[АвторСделки],p.Period_
"""


def ratio(fact, plan):
    return fact / plan if plan > 0 else None


def explain(detail_rows, gate_rows, threshold):
    threshold = decimal(threshold)
    author_gates = {}
    branch_gates = defaultdict(lambda: [Decimal(0)] * 3)
    for kind, branch, author, code, plan, legacy, fact in gate_rows:
        key = (kind, branch, str(author or '').strip() or 'Не заполнено', str(code))
        values = tuple(decimal(v or 0) for v in (plan, legacy, fact))
        author_gates[key] = values
        branch_key = (kind, branch, str(code))
        branch_gates[branch_key] = [a + b for a, b in zip(branch_gates[branch_key], values)]
    result = []
    for kind, branch, author, planning, goods, brand, formula, period, plan, fact, possible, paid, bonus in detail_rows:
        author = str(author or '').strip() or 'Не заполнено'
        code = '5' if str(period).strip().casefold() == 'год' else str(period).strip()[:1]
        plan, fact, possible, paid, bonus = (decimal(v or 0) for v in (plan, fact, possible, paid, bonus))
        global_mode = formula == 'Подразделение(Глобально)'
        personal = author_gates.get((kind, branch, author, code))
        department = branch_gates.get((kind, branch, code))
        department_ratio = ratio(department[2], department[0 if global_mode else 1]) if department else None
        personal_ratio = ratio(personal[2], personal[1]) if personal and not global_mode else None
        execution = ratio(fact, plan)
        if paid + bonus != 0:
            reason = 'К выплате'
        elif possible == 0:
            reason = 'Факт проверяемого периода равен нулю' if fact == 0 else 'Начисленная премия равна нулю; проверить ставки и корректировки'
        elif code == '1':
            reason = 'Начисление есть, выплата равна нулю; проверить корректировки'
        else:
            blocked = []
            if execution is None:
                blocked.append('выполнение по формуле не определено: нулевой или отрицательный план')
            elif execution < threshold:
                blocked.append('выполнение по формуле ниже порога')
            if not global_mode:
                if department_ratio is not None and department_ratio < threshold:
                    blocked.append('выполнение подразделения ниже порога')
                if personal_ratio is not None and personal_ratio < threshold:
                    blocked.append('выполнение сотрудника ниже порога')
            reason = '; '.join(blocked) if blocked else 'По показанным процентам блокировки нет; проверить ставки и корректировки'
        if author=='Не распределён' and possible==0 and paid+bonus==0:
            reason='План сохранён без распределения: нет исторических итогов авторов подразделения; премия равна нулю'
        if code == '1':
            reason += '. I квартал: порог выплаты не применяется'
        result.append({'kind': kind, 'branch': branch, 'author': author, 'planning': planning,
            'goods': goods, 'brand': brand, 'formula': formula or 'Подразделение', 'period': period,
            'plan': plan, 'fact': fact, 'ratio': execution, 'threshold': None if code == '1' else threshold,
            'department_ratio': department_ratio, 'author_ratio': personal_ratio,
            'department_plan': department[0] if department else None,
            'department_fact': department[2] if department else None,
            'possible': possible, 'paid': paid, 'bonus': bonus, 'total': paid + bonus, 'reason': reason})
    return result


from .recalculation_2025_performance_excel import append_sheets
