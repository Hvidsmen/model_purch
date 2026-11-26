"""Durable, manually launched shareholder scenarios; only local temp SQL writes."""
from datetime import date,datetime,timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID,uuid4
import json,logging,os,re,subprocess,sys
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction,close_old_connections
from django.utils import timezone
from ..models import ShareholderCalculation,ShareholderSnapshot
from .shareholder_allocation import author_weights
from .shareholder_sql import private_sql,temporary_tables,fingerprint
from . import shareholder_regions as regions

logger=logging.getLogger(__name__)
from .shareholder_original_rules import RULE,enabled,performance_sql as original_performance_sql


def serial(value):
    if isinstance(value,dict):return {str(k):serial(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [serial(v) for v in value]
    if isinstance(value,(Decimal,date,datetime,UUID)):return str(value)
    if isinstance(value,(bytes,bytearray,memoryview)):return bytes(value).hex()
    return value


@transaction.atomic
def create(configuration,user):
    from .calculation_jobs import calculation_command
    job=configuration['source_job']
    calculation_command(job,locked=False,include_separate=False)
    parameters={key:str(getattr(job,key)) for key in ('fact_year','plan_version','quarter_share','year_share','payout_threshold')}
    return ShareholderCalculation.objects.create(name=configuration['name'],as_of=configuration['as_of'],scenario=configuration['scenario'],
        quarters=sorted(int(v) for v in configuration['quarters']),threshold_percent=configuration['threshold_percent'],source_job=job,created_by=user,
        configuration={**parameters,'rule':RULE,'source_job_key':str(job.key),
            'regional_rule':regions.RULE,'regional_rates':regions.approved_rates()})


def launch(run):
    directory=Path(settings.BASE_DIR)/'.local'/'shareholder-runs'
    env={**os.environ,'DJANGO_SETTINGS_MODULE':settings.SETTINGS_MODULE,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}
    kwargs={'creationflags':subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    try:
        directory.mkdir(parents=True,exist_ok=True)
        with (directory/(str(run.pk)+'.log')).open('ab') as log:
            subprocess.Popen([sys.executable,str(Path(settings.BASE_DIR)/'manage.py'),'run_shareholder_calculation','--run',str(run.pk)],cwd=settings.BASE_DIR,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True,**kwargs)
    except Exception:
        logger.exception('Shareholder worker launch failed: %s',run.pk)
        ShareholderCalculation.objects.filter(pk=run.pk,state='queued').update(state='failed',error='Не удалось запустить отдельный процесс расчёта.',stage='Ошибка запуска',completed_at=timezone.now())


def progress(run,text):ShareholderCalculation.objects.filter(pk=run.pk,state='running').update(stage=text)


def save_query(run,cursor,mode,dataset,sql,args=(),kind=None):
    cursor.execute(sql,*args) if args else cursor.execute(sql)
    fields=[column[0] for column in cursor.description]
    from django.db.models import Max
    previous=ShareholderSnapshot.objects.filter(calculation=run,mode=mode,dataset=dataset).aggregate(value=Max('sequence'))['value']
    sequence=previous+1 if previous is not None else 0;count=0
    while True:
        rows=cursor.fetchmany(500)
        if not rows:break
        payload=[]
        for row in rows:
            value=dict(zip(fields,row))
            if kind:value['ТипКоэф']=kind
            payload.append(serial(value))
            if dataset.startswith('report_') and run.scenario=='combined':
                from .shareholder_reports import OPTION_FIELDS,period_code
                if period_code(value.get('КварталМотивации','')) in [*map(str,run.quarters),'5']:
                    choices=getattr(run,'_report_options',None)
                    if choices is None:choices={key:set() for key in OPTION_FIELDS};run._report_options=choices
                    if value.get('АвторСделки') and value.get('Подразделение'):
                        if not hasattr(run,'_author_subdivisions'):run._author_subdivisions={}
                        run._author_subdivisions.setdefault(str(value['АвторСделки']),set()).add(str(value['Подразделение']))
                    for key,field in OPTION_FIELDS.items():
                        if value.get(field):choices[key].add(str(value[field]))
        ShareholderSnapshot.objects.create(calculation=run,mode=mode,dataset=dataset,sequence=sequence,payload=payload)
        sequence+=1;count+=len(payload)
    return count


def prepare_mapping(cursor,run):
    quarters=','.join(str(int(q)) for q in run.quarters)
    scope=regions.BRANCH_SQL if regions.enabled(run) else "N'Д-Москва',N'ТЕРМОТРЕЙД'"
    quarter_condition=('sub.SubdivisionName IN ('+regions.REGION_SQL+') OR ' if regions.enabled(run) else '')
    cursor.execute('''SELECT COUNT(*) FROM (SELECT SubdivisionKey FROM dbo.Subdivisions
 WHERE SubdivisionKey IN(SELECT SubdivisionKey FROM dbo.Subdivisions WHERE SubdivisionName IN('''+scope+''')) GROUP BY SubdivisionKey HAVING COUNT(*)>1) duplicates''')
    if cursor.fetchone()[0]:raise ValidationError('В справочнике повторяются ключи подразделений расчёта.')
    cursor.execute('''SELECT sub.SubdivisionName,COALESCE(LTRIM(RTRIM(s.[Даичи_АвторСделкиНаименование])),N''),
 SUM(CASE WHEN '''+quarter_condition+'''DATEPART(QUARTER,s.ClosedDealDate) IN ('''+quarters+''') THEN s.Price-s.AmountUSDIt_ITW+s.BallsOuterUSD ELSE 0 END)
 FROM motivation.PortalPreparedSales s JOIN dbo.Subdivisions sub ON sub.SubdivisionKey=s.Subdivision
 WHERE s.ClosedDealDate>=? AND s.ClosedDealDate<? AND sub.SubdivisionName IN('''+scope+''')
 AND EXISTS(SELECT 1 FROM dbo.Product p WHERE p.ProductCode=s.ProductCode AND p.[Тип номенклатуры]=N'Товар')
 AND EXISTS(SELECT 1 FROM dbo.Customer c WHERE c.CustomerKey=s.CustomerKey)
 GROUP BY sub.SubdivisionName,COALESCE(LTRIM(RTRIM(s.[Даичи_АвторСделкиНаименование])),N'')''',date(2026,1,1),run.as_of+timedelta(days=1))
    weights=author_weights(cursor.fetchall(),run.threshold_percent)
    cursor.execute('''CREATE TABLE #share_author_map(SourceBranch nvarchar(255) COLLATE DATABASE_DEFAULT,OriginalAuthor nvarchar(255) COLLATE DATABASE_DEFAULT,
 AssignedAuthor nvarchar(255) COLLATE DATABASE_DEFAULT,Weight_ decimal(38,24));''')
    if weights:cursor.executemany('INSERT INTO #share_author_map VALUES (?,?,?,?)',[(v['branch'],v['original'],v['assigned'],format(v['weight'],'.24f')) for v in weights])
    return weights


def execute(run,connector=None):
    from ..conns import connect_database
    from .manager_detail import grouped_sql
    from .motivation_performance import grouped_sql as performance_sql,department_sql
    connection=None
    try:
        progress(run,'Подключение и проверка общего подготовленного факта')
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH');connection.autocommit=True;connection.timeout=1800
        cursor.execute("DECLARE @r int;EXEC @r=sys.sp_getapplock @Resource=N'PortalMotivationCalculation',@LockMode=N'Shared',@LockOwner=N'Session',@LockTimeout=0;SELECT @r;")
        if cursor.fetchone()[0]<0:raise ValidationError('Сейчас идёт подготовка факта или общий расчёт. Повторите отдельное задание позже.')
        cursor.execute("SELECT r.RunId,r.CompletedAt,r.RowCount_ FROM motivation.PortalPreparedSalesActive a JOIN motivation.PortalPreparedSalesRun r ON r.RunId=a.RunId WHERE a.Id=1 AND r.State_=N'completed'")
        ready=cursor.fetchone()
        if not ready:raise ValidationError('Нет опубликованного подготовленного факта.')
        definitions={}
        for mode,name in [('combined','PortalCalcAll2026'),('separate','PortalCalcSeparate2026')]:
            if mode=='separate' and run.scenario!='separate':continue
            cursor.execute('SELECT OBJECT_DEFINITION(OBJECT_ID(?))','motivation.'+name)
            row=cursor.fetchone();definitions[mode]=row[0] if row else None
        if not definitions['combined']:raise ValidationError('Не установлено задание «Все кварталы 2026». Обновите его через Центр расчётов.')
        if regions.enabled(run):
            profiles={regions.uses_native_key_functions(value) for value in definitions.values()}
            if len(profiles)!=1:raise ValidationError('Установленные процедуры используют разные ключи коэффициентов. Обновите задания расчёта 2026 перед новым запуском.')
            regions.install_rates(cursor,run,definitions['combined'])
        progress(run,'Определение долей авторов по исходным подразделениям')
        weights=prepare_mapping(cursor,run)
        cursor.execute('''CREATE TABLE #share_parameters(FactYear int,PlanVersion nvarchar(500),QuarterShare decimal(9,8),YearShare decimal(9,8),PayoutThreshold decimal(9,8),Cutoff date);''')
        config=run.configuration
        cursor.execute('INSERT INTO #share_parameters VALUES (?,?,?,?,?,?)',2026,config['plan_version'],str(Decimal(config['quarter_share'])/100),str(Decimal(config['year_share'])/100),config['payout_threshold'],run.as_of+timedelta(days=1))
        metadata={'manager_correction_mode':'legacy' if any('#portal_manager_correction' in str(v).casefold() for v in definitions.values()) else 'period-unified-v1','rule':config.get('rule','shareholder-asof-2026-v1'),'prepared_run':str(ready[0]),'prepared_at':str(ready[1]),'prepared_rows':ready[2],
                  'procedure_hashes':{},'counts':{},'redistribution':'separate-original-subdivision-before-coefficients',
                  'turnover_basis':'Price - AmountUSDIt_ITW + BallsOuterUSD; signed; selected quarters through as_of',
                  'quarter_rule':('Q1 always paid; Q2 eligibility Q1+Q2; Q3 Q3; Q4 and year Q1–Q4; annual contributions Q1–Q4; year-end catch-up' if enabled(run) else 'I–III separate if selected; IV and annual Moscow+TT combined')}
        if regions.enabled(run):
            metadata.update(regional_rule=regions.RULE,branches=list(regions.BRANCHES),regional_plan_basis='all 2026 through cutoff, after reassignment; per subdivision',regional_rate_source='approved portal versions frozen at creation')
        for mode,definition in definitions.items():
            batch=private_sql(definition,mode,run.pk,original_rules=enabled(run),regional_policy=regions.enabled(run))
            for name in temporary_tables(batch):
                if name in ('#share_parameters','#share_author_map'):continue
                cursor.execute("IF OBJECT_ID('tempdb.."+name+"') IS NOT NULL DROP TABLE "+name)
            progress(run,'Перераспределение и пересчёт премий · '+('Москва + ТТ' if mode=='combined' else 'Москва и ТТ раздельно'))
            cursor.execute(batch)
            prepared=None
            while True:
                if cursor.description and cursor.description[0][0]=='SharePreparedRun':prepared=str(cursor.fetchone()[0])
                if not cursor.nextset():break
            if prepared!=str(ready[0]):raise ValidationError('Во время расчёта изменился подготовленный факт. Результаты не опубликованы.')
            if regions.enabled(run) and mode=='combined':
                save_query(run,cursor,mode,'policy_plan_shares','SELECT * FROM #share_plan_weights')
                save_query(run,cursor,mode,'policy_rates','SELECT * FROM #share_region_coefficients')
                save_query(run,cursor,mode,'policy_defaults','SELECT * FROM #share_region_defaults')
            metadata['procedure_hashes'][mode]=fingerprint(definition)
            progress(run,'Сохранение согласованных снимков пяти отчётов · '+mode)
            counts={}
            for dataset in ('report','performance','department','annual','eligibility'):counts[dataset]=0
            # Store all coefficient kinds without multiplying different bases together.
            for kind in ('Политики','Продажи','Директора'):
                source="SELECT * FROM #share_report WHERE LTRIM(RTRIM([ТипКоэф]))=N'"+kind+"'"
                # Sequence keys are per dataset, so each kind has its own named dataset.
                tag={'Политики':'pol','Продажи':'sales','Директора':'dir'}[kind]
                eligibility_count=0
                if enabled(run):
                    eligibility_count=save_query(run,cursor,mode,'eligibility_'+tag,"SELECT * FROM #share_eligibility WHERE LTRIM(RTRIM([ТипКоэф]))=N'"+kind+"'")
                report_count=save_query(run,cursor,mode,'report_'+tag,grouped_sql(source),kind=kind)
                if enabled(run) and report_count and not eligibility_count:
                    raise ValidationError('Не сохранено годовое условие выплаты акционеров. Результаты не опубликованы.')
                counts['report']+=report_count;counts['eligibility']+=eligibility_count
                query=performance_sql(source,coefficient_type=kind).replace('DataWH.motivation.PlanFactAll2026','#share_plan_fact')
                if enabled(run):query=original_performance_sql(query)
                if regions.enabled(run) and kind=='Политики':query=regions.performance_sql(query)
                counts['performance']+=save_query(run,cursor,mode,'perf_'+tag,query,kind=kind)
                query=department_sql(source).replace('DataWH.motivation.PlanFactAll2026','#share_plan_fact')
                if enabled(run):query=original_performance_sql(query)
                counts['department']+=save_query(run,cursor,mode,'dept_'+tag,query,kind=kind)
                counts['annual']+=save_query(run,cursor,mode,'annual_'+tag,"SELECT * FROM #share_annual WHERE LTRIM(RTRIM([ТипКоэф]))=N'"+kind+"'")
            # Redistribution is identical in both modes; keep one transaction audit.
            counts['mapping']=counts['deals']=0
            if mode=='combined':
                counts['mapping']=save_query(run,cursor,mode,'mapping','SELECT * FROM #share_map')
                counts['deals']=save_query(run,cursor,mode,'deals','''SELECT ShareSourceBranch AS SourceBranch,ShareSourceRow AS SourceRow,
     ShareOriginalAuthor AS OriginalAuthor,[Даичи_АвторСделкиНаименование] AS AssignedAuthor,ShareWeight AS Weight_,
     s.ClosedDealDate AS ClosedDate,s.DocumentNumber,s.ProductCode,
     p.PlanningGroupSalesErp AS [ГруппаПлановПродаж],
     CASE WHEN p.PlanningGroupSalesErp IN(N'1. RAC',N'2. VRF',N'3. PROF') THEN p.GroupERP ELSE N'' END AS [ГруппаТоваров],
     CASE WHEN p.GroupERP IN(N'Бытовые кондиционеры',N'Полупромышленные кондиционеры',N'Системы мульти-сплит',N'Системы VRF') THEN p.BrandName ELSE N'' END AS [Марка],
     Price-AmountUSDIt_ITW+BallsOuterUSD AS AllocatedUSD,
     (Price-AmountUSDIt_ITW+BallsOuterUSD)*PriceRUR/IIF(Price=0,1,Price) AS AllocatedRUB
     FROM #share_transformed s JOIN dbo.Product p ON p.ProductCode=s.ProductCode AND p.[Тип номенклатуры]=N'Товар'
     WHERE EXISTS(SELECT 1 FROM dbo.Customer c WHERE c.CustomerKey=s.CustomerKey)''')
            metadata['counts'][mode]=counts
        from .curator_sql import shareholder_snapshots
        curator_count=shareholder_snapshots(run,cursor,definitions['combined'],save_query)
        metadata['counts']['combined']['report']+=curator_count
        metadata['curator_rows']=curator_count
        if not any(item['report'] for item in metadata['counts'].values()):raise ValidationError('Расчёт не вернул строк. Пустой результат не подставлен.')
        if hasattr(run,'_report_options'):
            from .shareholder_reports import OPTIONS_KEY
            metadata[OPTIONS_KEY]={key:sorted(values) for key,values in run._report_options.items()}
            metadata[OPTIONS_KEY]['author_subdivisions']={author:sorted(getattr(run,'_author_subdivisions',{}).get(author,())) for author in metadata[OPTIONS_KEY]['author']}
        ShareholderCalculation.objects.filter(pk=run.pk,state='running').update(state='completed',stage='Готово · все отчёты используют один снимок',metadata=serial(metadata),completed_at=timezone.now())
    finally:
        if connection is not None:connection.close()


def run_calculation(pk,connector=None):
    with transaction.atomic():
        run=ShareholderCalculation.objects.select_for_update().get(pk=pk)
        if run.state!='queued':raise ValidationError('Этот запуск уже принят в работу. Создайте новый расчёт.')
        run.state='running';run.started_at=timezone.now();run.stage='Запуск';run.save(update_fields=['state','started_at','stage'])
    try:execute(run,connector)
    except Exception as error:
        reference=uuid4().hex[:12]
        logger.exception('Shareholder calculation %s failed, reference %s',run.pk,reference)
        reason=' '.join(error.messages) if isinstance(error,ValidationError) else 'Подробности в журнале отдельного задания.'
        if not isinstance(error,ValidationError):
            if re.search(r'\(50071\)',str(error)):reason='Перераспределение не сохранило оборот USD/RUB. Результаты не опубликованы.'
            elif re.search(r'\(5008[234]\)',str(error)):reason='Не удалось проверить доли, сохранность плана или утверждённые коэффициенты Ростова/Питера. Результаты не опубликованы.'
            elif re.search(r'\(50081\)',str(error)):reason='Не найдены выгруженные ставки куратора. Проверьте назначения и выгрузку утверждённой версии.'
            elif re.search(r'\(50040\)',str(error)):reason='Нет совместимого подготовленного факта. Обновите подготовку в Центре расчётов.'
        ShareholderCalculation.objects.filter(pk=run.pk,state='running').update(state='failed',stage='Расчёт остановлен',error=reason+' Код: '+reference,completed_at=timezone.now())
        # Incomplete financial rows are never useful report data.
        ShareholderSnapshot.objects.filter(calculation=run).delete()
    finally:close_old_connections()
