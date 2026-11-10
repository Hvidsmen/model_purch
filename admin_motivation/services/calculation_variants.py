"""Fixed calculation namespaces; no report parameter can select an arbitrary table."""
import re
from django.core.exceptions import ValidationError
from django.db.models import Q

MAIN = 'main'
ALL_2026 = 'all_2026'
SEPARATE_2026 = 'separate_2026'
TEST_2025 = 'test_2025'
CHOICES = [(MAIN, 'Основной · премия только за IV квартал'), (ALL_2026, 'Дополнительный · все кварталы 2026')]
TABLES = ('Fact', 'PlanFact', 'ReportMotivationByDealAuthor', 'AnnualAccrual')


def variant_of(job):
    value = getattr(job, 'variant', MAIN)
    if value not in (MAIN, ALL_2026, SEPARATE_2026, TEST_2025):
        raise ValidationError('Неизвестный вариант расчёта.')
    if value in (ALL_2026, SEPARATE_2026) and job.fact_year != 2026:
        raise ValidationError('Дополнительный вариант доступен только за 2026 год.')
    if value == TEST_2025 and job.fact_year != 2025:
        raise ValidationError('Тестовый пересчёт доступен только за 2025 год.')
    return value


def procedure(variant):
    if variant not in (MAIN, ALL_2026, SEPARATE_2026, TEST_2025):
        raise ValidationError('Неизвестный вариант расчёта.')
    return {MAIN: 'motivation.PortalCalcBySub', ALL_2026: 'motivation.PortalCalcAll2026', SEPARATE_2026: 'motivation.PortalCalcSeparate2026', TEST_2025: 'motivation.PortalCalcTest2025'}[variant]


def isolate(sql, suffix='All2026'):
    if suffix not in ('All2026', 'Separate2026'):
        raise ValueError('Unknown calculation namespace')
    for table in TABLES:
        pattern = r'(?:(?:\[?DataWH\]?)\s*\.\s*)?\[?motivation\]?\s*\.\s*\[?' + table + r'\]?(?!\w)'
        sql = re.sub(pattern, 'DataWH.motivation.' + table + suffix, sql, flags=re.I)
    return sql


def main_runs():
    from ..models import MotivationExecution
    return MotivationExecution.objects.filter(Q(parameters__variant__isnull=True) | Q(parameters__variant=MAIN))


def full_year_patch(definition):
    """All five output periods, without a second annual catch-up payment."""
    from .pilot_formula import mask_sql
    from .pilot_calculation import normalize_q4_rur
    definition = normalize_q4_rur(definition)
    from .manager_correction import TABLE
    mask = mask_sql(definition)
    outputs = re.findall(r"N?'([1-4]\s+Квартал[^']*|Год)'\s+(?:AS\s+)?\[?КварталМотивации\]?", definition, re.I)
    if ['5' if item.casefold() == 'год' else item[0] for item in outputs] != ['1', '2', '3', '4', '5', '5']:
        raise ValidationError('Не распознаны все кварталы и год дополнительного расчёта.')
    carry = list(re.finditer(r'\bINSERT\s+(?:INTO\s+)?' + TABLE + r'(?!\w)', mask, re.I))
    if len(carry) != 1:
        raise ValidationError('Не распознана годовая доплата дополнительного расчёта.')
    point = carry[0].start()
    return definition[:point] + '-- Separate 2026 variant: four quarters and annual, no catch-up.\nIF 1=0\n' + definition[point:]

