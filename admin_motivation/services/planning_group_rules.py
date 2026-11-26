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


@transaction.atomic
def bulk_save(actor, version_id, revision, selected, changes):
    from base.access import can_access
    from .directors import lock_editable
    from ..models import GlobalCoeffVersion
    if not can_access(actor, 'motivation_editor'):raise PermissionDenied
    version=lock_editable(version_id)
    if str(version.revision)!=str(revision):raise ValidationError('Версия изменилась. Обновите страницу.')
    rows=deepcopy(version.planning_rules)
    try:
        indexes={int(value) for value in selected}
        if not indexes or any(i<0 or i>=len(rows) for i in indexes):raise ValueError
    except (ValueError,TypeError):raise ValidationError('Выберите правила из текущей версии.')
    if not changes:raise ValidationError('Выберите, что изменить.')
    if set(changes)-{'formula','missing','k0','k1','k2','k3','k4'}:raise ValidationError('Некорректные поля массового изменения.')
    for i in indexes:
        rows[i].update(changes)
        if rows[i]['missing']!='fixed':
            for k in range(5):rows[i].pop('k'+str(k),None)
    validate(rows)
    version.planning_rules=rows
    version.save(update_fields=['planning_rules'])
    GlobalCoeffVersion.objects.filter(pk=version.pk).update(revision=version.revision+1)
    return len(indexes)


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
    if '__PORTAL_GROUP_RULES__' in source:
        if re.search(r"CONVERT\s*\(\s*float\s*,\s*N?'Не задан коэффициент группы:",source,re.I):
            raise ValidationError('Установлена старая процедура подбора ставок. Обновите задание «Все кварталы 2026» в Центре расчётов и создайте новый акционерный запуск.')
        return source
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
            value=f"CASE gr.Missing WHEN N'minimum' THEN {minimum} WHEN N'fixed' THEN gr.{field} ELSE NULL END"
            fallback='(SELECT TOP(1) '+value+lookup+')'
            method='(SELECT TOP(1) gr.Missing'+lookup+')'
            edits.append((start,stop+1,f"CASE WHEN {method} IN(N'minimum',N'fixed',N'error') THEN COALESCE({exact},{fallback}) ELSE COALESCE({exact},{native}) END"))
        previous=scope_end
    for start,end,value in sorted(edits,reverse=True):source=source[:start]+value+source[end:]
    return '-- __PORTAL_GROUP_RULES__\n'+validate_missing_rates(source)


def validate_missing_rates(source):
    """Fail explicitly after input materialization, before payout windows/deletes.

    Missing configured rates remain NULL rather than falling through to native
    defaults. Never use an invalid conversion as a conditional SQL error.
    """
    code=mask_sql(source)
    if not re.search(r'\bINTO\s+#pf_base\b',code,re.I):return source
    boundary=re.search(r'\bDELETE\s+(?:FROM\s+)?#pf_base\b',code,re.I)
    if not boundary:raise ValidationError('Не найдена граница проверки ставок правил групп.')
    guard="""
-- __PORTAL_GROUP_RATE_VALIDATION__
DECLARE @PortalGroupRateError nvarchar(2048);
SELECT TOP(1) @PortalGroupRateError=CONCAT(N'PG_RULE_RATE_MISSING: subdivision=',[Подразделение],
 N'; kind=',[ТипКоэф],N'; group=',[ГруппаПлановПродаж],N'; product=',[ГруппаТоваров],N'; brand=',[Марка],
 N'')
FROM #pf_base WHERE K0 IS NULL OR K1 IS NULL OR K2 IS NULL OR K3 IS NULL OR K4 IS NULL;
IF @PortalGroupRateError IS NOT NULL THROW 50087,@PortalGroupRateError,1;
"""
    return source[:boundary.start()]+guard+source[boundary.start():]


def missing_rate_message(error):
    text=str(error)
    match=re.search(r'PG_RULE_RATE_MISSING: ([^\r\n]{1,1600}?)\s*\(50087\)',text)
    if match:
        detail=match[1]
        try:detail=detail.encode('cp1251').decode('utf-8')
        except (UnicodeEncodeError,UnicodeDecodeError):pass
        for key,label in (('subdivision=','Подразделение: '),('kind=','Вид: '),('group=','Группа: '),('product=','Товар: '),('brand=','Бренд: ')):
            detail=detail.replace(key,label)
        return 'Не найдена ставка по правилу группы. '+detail+'. Проверьте точные ставки и подбор K0–K4.'
    if '(50087)' in text:return 'Не найдена ставка по правилу группы. Проверьте точные ставки и подбор K0–K4. Подробности в журнале задания.'
    return None
