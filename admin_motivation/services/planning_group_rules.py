"""Versioned formula overrides and missing-rate policy by planning group."""
from copy import deepcopy
from decimal import Decimal,InvalidOperation
import re
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from .pilot_formula import mask_sql,closing

FORMULAS=('Группа планов продаж','Группа товаров','Марка(Бренд)','Подразделение','Подразделение(Глобально)')
KINDS=('Политики','Продажи','Директора','Кураторы')
METHODS=(('auto','Текущий автоматический подбор'),('minimum','Минимум внутри группы'),('fixed','Заданные K0–K4'),('error','Не рассчитывать без точной ставки'))
TABLE='motivation.PortalPlanningGroupRules'
TABLE_SQL='''IF OBJECT_ID(N'motivation.PortalPlanningGroupRules',N'U') IS NULL
CREATE TABLE motivation.PortalPlanningGroupRules(Date_ date NOT NULL,Subdivision nvarchar(255) NOT NULL,
TypeCoeff nvarchar(40) NOT NULL,PGSales nvarchar(255) NOT NULL,Formula nvarchar(64) NULL,Missing nvarchar(16) NOT NULL,
K0 float NULL,K1 float NULL,K2 float NULL,K3 float NULL,K4 float NULL);'''
FIELDS=('Date_','Subdivision','TypeCoeff','PGSales','Formula','Missing','K0','K1','K2','K3','K4')


def for_curator(source):
    """Director SQL is reused internally; group rules must still be curator rules.

    Rate rows in #curator_coeff have already been renamed to Директора. Only
    the group-rule lookup changes type; its minimum stays on those own rates.
    """
    pattern=r"\bgr\.TypeCoeff=(N?'[^']*'|[\w\[\].]+)(?=\s+AND\s+gr\.Subdivision)"
    source=re.sub(pattern,lambda m:"gr.TypeCoeff=CASE WHEN ("+m[1]+")=N'Директора' THEN N'Кураторы' ELSE ("+m[1]+") END",source,flags=re.I)
    return source.replace('SELECT TOP(1) gr.TypeCoeff',"SELECT TOP(1) CASE WHEN gr.TypeCoeff=N'Кураторы' THEN N'Директора' ELSE gr.TypeCoeff END")


def validate(rows):
    if not isinstance(rows,list):raise ValidationError('Правила групп должны быть списком.')
    keys=set()
    for row in rows:
        if not isinstance(row,dict):raise ValidationError('Некорректное правило группы.')
        if row.get('formula','') not in ('',*FORMULAS) or row.get('missing') not in dict(METHODS):raise ValidationError('Выберите формулу и способ подбора.')
        if row.get('kind') not in KINDS:raise ValidationError('Выберите вид коэффициента.')
        if not str(row.get('planning') or '').strip():raise ValidationError('Укажите группу планов продаж.')
        for field in ('subdivision','planning'):
            if not isinstance(row.get(field,''),str) or len(row.get(field,''))>255:raise ValidationError('Некорректное название группы или подразделения.')
        key=tuple(str(row.get(k,'')).strip().casefold() for k in ('subdivision','kind','planning'))
        if key in keys:raise ValidationError('Повтор правила группы для подразделения и вида мотивации.')
        keys.add(key)
        if row['missing']=='fixed':
            try:
                values=[Decimal(str(row['k'+str(i)])) for i in range(5)]
                if any(not v.is_finite() or v<0 or v>1 for v in values):raise ValueError
            except (InvalidOperation,KeyError,ValueError):raise ValidationError('Заполните резервные K0–K4 от 0 до 100%.')
    return rows


@transaction.atomic
def save(actor,version_id,revision,row,delete=False):
    from base.access import can_access
    from .directors import lock_editable
    from ..models import GlobalCoeffVersion
    if not can_access(actor,'motivation_editor'):raise PermissionDenied
    version=lock_editable(version_id)
    if str(version.revision)!=str(revision):raise ValidationError('Версия изменилась. Обновите страницу.')
    incoming=[dict(row,kind=kind) for kind in KINDS] if row.get('kind')=='all' else [row]
    validate(incoming);key=lambda r:tuple(str(r.get(k,'')).strip().casefold() for k in ('subdivision','kind','planning'))
    keys={key(r) for r in incoming}
    rows=[r for r in version.planning_rules if key(r) not in keys]
    if not delete:rows.extend(incoming)
    validate(rows);version.planning_rules=rows;version.save(update_fields=['planning_rules'])
    GlobalCoeffVersion.objects.filter(pk=version.pk).update(revision=version.revision+1)
    version.refresh_from_db();return version


def frozen(versions=None):
    if versions is None:
        from ..models import GlobalCoeffVersion
        versions=GlobalCoeffVersion.objects.filter(status='approved').order_by('effective_from','pk')
    return [dict(date=v.effective_from.isoformat(),rules=deepcopy(validate(v.planning_rules))) for v in versions]


def tuples(snapshot):
    for version in snapshot:
        # A sentinel retains empty versions: deleting a rule must stop inheriting it.
        yield (version['date'],'','','',None,'auto',None,None,None,None,None)
        for row in validate(version['rules']):
            yield (version['date'],row.get('subdivision',''),row['kind'],row['planning'],row.get('formula') or None,row['missing'],
                   *[row.get('k'+str(i)) if row['missing']=='fixed' else None for i in range(5)])


def export(cursor,versions):
    cursor.execute(TABLE_SQL);cursor.execute('DELETE FROM '+TABLE)
    values=list(tuples(frozen(versions)))
    if values:cursor.executemany('INSERT INTO '+TABLE+' VALUES('+','.join('?' for _ in FIELDS)+')',values)


def install(cursor,snapshot):
    cursor.execute(TABLE_SQL[TABLE_SQL.index('CREATE TABLE'):].replace(TABLE,'#share_group_rules'))
    rows=list(tuples(snapshot))
    if rows:cursor.executemany('INSERT INTO #share_group_rules VALUES('+','.join('?' for _ in FIELDS)+')',rows)


def patch(source,table=TABLE,minimum_source='DataWH.motivation.vSubdivisionMotiveCoeffTo'):
    """Override formulas; retain exact rates before any configured missing rate."""
    if '__PORTAL_GROUP_RULES__' in source:return source
    from .shareholder_regions import _query_branch_end
    code=mask_sql(source)
    if minimum_source=='DataWH.motivation.vSubdivisionMotiveCoeffTo' and re.search(r'\bINTO\s+#coeff_source\b',code,re.I):minimum_source='#coeff_source'
    matches=list(re.finditer(r'\bCOALESCE\s*\(\s*sc\s*\.\s*\[?VariationCalculate\]?\s*,',code,re.I))
    if not matches:raise ValidationError('Не найдены формулы для правил групп. Обновите исходную процедуру.')
    edits=[];previous=0
    for match in matches:
        end=closing(code,code.index('(',match.start()))
        scope_end=_query_branch_end(code,end+1)
        tail=source[end+1:scope_end]
        date=re.search(r'([\w\[\].]+)\s*(?:BETWEEN|>=)\s*sc\.\[?DateFrom\]?',tail,re.I)
        def dimension(field):
            name=r'sc\.\[?'+field+r'\]?'
            direct=re.search(r'([\w\[\].]+)\s*=\s*'+name+r'\b',tail,re.I)
            reverse=re.search(name+r'\s*=\s*([\w\[\].]+)',tail,re.I)
            return direct[1] if direct else reverse[1] if reverse else None
        branch,planning=dimension('Subdivision'),dimension('PGSales')
        if not branch:
            boundary=re.search(r'=\s*sc\.\[?Subdivision\]?',tail,re.I)
            names=re.findall(r'[\w\[\]]+\.\[?SubdivisionName\]?',tail[:boundary.start()] if boundary else '',re.I)
            branch=names[-1] if names else None
        kind=re.search(r'(?:LTRIM\(RTRIM\(|TRIM\()?sc\.\[?(?:NormalizedTypeCoeff|TypeCoeff)\]?\)*\s*=\s*(N?\x27[^\x27]*\x27|[\w\[\].]+)',tail,re.I)
        if not all((date,branch,planning,kind)):raise ValidationError('Не распознаны ключи группы в соединении коэффициентов.')
        on_date=date[1];kind=kind[1]
        lookup=(f' FROM {table} gr WHERE gr.Date_=(SELECT MAX(gd.Date_) FROM {table} gd WHERE gd.Date_<={on_date})'
                f" AND gr.PGSales={planning} AND gr.TypeCoeff={kind} AND gr.Subdivision IN(N'',{branch}) ORDER BY CASE WHEN gr.Subdivision={branch} THEN 0 ELSE 1 END")
        for projection in re.finditer(r'TRIM\(sc\.TypeCoeff\)(\s+TypeCoeff_)',source[previous:scope_end],re.I):
            edits.append((previous+projection.start(),previous+projection.end(),'COALESCE(TRIM(sc.TypeCoeff),(SELECT TOP(1) gr.TypeCoeff'+lookup+'))'+projection[1]))
        formula='(SELECT TOP(1) gr.Formula'+lookup+')'
        edits.append((match.start(),end+1,'COALESCE('+formula+','+source[match.start():end+1]+')'))
        for rate in re.finditer(r'\bCOALESCE\s*\(\s*sc\.\[?(K[0-4])\]?\s*,',code[previous:scope_end],re.I):
            start=previous+rate.start();stop=closing(code,code.index('(',start));field=rate[1]
            original=source[start:stop+1];comma=previous+rate.end()-1
            exact=source[code.index('(',start)+1:comma].strip();native=source[comma+1:stop]
            dates=f'{on_date} BETWEEN grc.DateFrom AND grc.DateTo'
            if re.search(r'<\s*sc\.\[?DateTo\]?',tail,re.I):dates=f'{on_date}>=grc.DateFrom AND {on_date}<grc.DateTo'
            minimum=(f'(SELECT MIN(grc.{field}) FROM {minimum_source} grc WHERE grc.PGSales={planning}'
                     f' AND grc.Subdivision={branch} AND LTRIM(RTRIM(grc.TypeCoeff))={kind} AND {dates})')
            error=f"CONVERT(float,N'Не задан коэффициент группы: '+gr.PGSales)"
            value=f"CASE gr.Missing WHEN N'minimum' THEN COALESCE({minimum},{error}) WHEN N'fixed' THEN gr.{field} WHEN N'error' THEN {error} ELSE NULL END"
            fallback='(SELECT TOP(1) '+value+lookup+')'
            edits.append((start,stop+1,f'COALESCE({exact},{fallback},{native})'))
        previous=scope_end
    for start,end,value in sorted(edits,reverse=True):source=source[:start]+value+source[end:]
    return '-- __PORTAL_GROUP_RULES__\n'+source
