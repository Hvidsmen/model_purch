"""Versioned fallback formula; exact coefficients and monetary rates stay intact."""
import re
from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import GlobalCoeffVersion
from .pilot_formula import mask_sql, closing

TABLE_SQL = """IF OBJECT_ID(N'motivation.PortalFormulaDefaults',N'U') IS NULL
CREATE TABLE motivation.PortalFormulaDefaults (Date_ date NOT NULL PRIMARY KEY, DefaultFormula nvarchar(64) NULL);"""


def export_defaults(cursor, versions):
    cursor.execute(TABLE_SQL)
    cursor.execute('DELETE FROM motivation.PortalFormulaDefaults WHERE Date_>=?', versions[0].effective_from)
    cursor.executemany('INSERT INTO motivation.PortalFormulaDefaults (Date_,DefaultFormula) VALUES (?,?)',
        [(row.effective_from, row.fallback_variation or None) for row in versions])


@transaction.atomic
def save_default(actor, version_id, revision, value):
    from base.access import can_access
    from django.core.exceptions import PermissionDenied
    from .directors import lock_editable
    if not can_access(actor, 'motivation_editor'):
        raise PermissionDenied
    version = lock_editable(version_id)
    if str(version.revision) != str(revision):
        raise ValidationError('Версия изменилась. Обновите страницу перед сохранением.')
    if value not in dict(GlobalCoeffVersion._meta.get_field('fallback_variation').choices):
        raise ValidationError('Выберите поддерживаемую формулу.')
    if version.fallback_variation != value:
        version.fallback_variation = value
        version.save(update_fields=['fallback_variation'])
        GlobalCoeffVersion.objects.filter(pk=version.pk).update(revision=version.revision + 1)
    version.refresh_from_db()
    return version


def patch_defaults(definition, required=False):
    """Extend only recognized formula defaults. Find the date in its coefficient join.

    Scope to the next formula projection so an unrecognized join cannot borrow a
    date from a different UNION branch. SQL strings/comments never match code.
    """
    mask = mask_sql(definition)
    pattern = re.compile(r'\b(?:COALESCE|ISNULL)\s*\(\s*(?P<alias>\[?\w+\]?)\s*\.\s*\[?VariationCalculate\]?\s*,', re.I)
    matches = list(pattern.finditer(mask))
    edits = []
    for index, match in enumerate(matches):
        end = closing(mask, mask.index('(', match.start()))
        scope_end = matches[index + 1].start() if index + 1 < len(matches) else len(mask)
        alias = re.escape(match['alias'])
        date_match = re.search(r'(?P<date>\[?\w+\]?\s*\.\s*\[?\w+\]?)\s*(?:\bBETWEEN\b|>=)\s*' + alias + r'\s*\.\s*\[?DateFrom\]?', mask[end:scope_end], re.I)
        if not date_match:
            raise ValidationError('Не распознана дата соединения коэффициентов для формулы по умолчанию. Исходная процедура не изменена.')
        # COALESCE accepts a third argument; convert ISNULL while keeping both existing expressions.
        opening = mask.index('(', match.start())
        first_argument = definition[opening + 1:match.end()-1].strip()
        old_default = definition[match.end():end].strip()
        date = date_match['date']
        fallback = f"(SELECT TOP (1) NULLIF(fd.DefaultFormula,N'') FROM motivation.PortalFormulaDefaults fd WHERE fd.Date_<={date} ORDER BY fd.Date_ DESC)"
        edits.append((match.start(), end + 1, f'COALESCE({first_argument},{fallback},{old_default})'))
    if required and not edits:
        raise ValidationError('В исходной SQL-процедуре не найдена поддерживаемая подстановка формулы. Расчёт не изменён.')
    for start, end, value in reversed(edits):
        definition = definition[:start] + value + definition[end:]
    return definition, len(edits)
