"""Bounded per-session snapshots; persist diagnostics only outside financial TX."""
TABLE='motivation.PortalCalculationStageProfile'
DDL=f"""IF OBJECT_ID(N'{TABLE}',N'U') IS NULL
 CREATE TABLE {TABLE}(RunId uniqueidentifier NOT NULL,Sequence_ int NOT NULL,
 Stage_ nvarchar(150) NOT NULL,StartedAt datetime2 NOT NULL,CompletedAt datetime2 NOT NULL,
 ElapsedMs bigint NOT NULL,CpuMs bigint NULL,LogicalReads bigint NULL,Reads_ bigint NULL,Writes_ bigint NULL,
 State_ nvarchar(20) NOT NULL,CONSTRAINT PK_PortalCalculationStageProfile PRIMARY KEY(RunId,Sequence_));"""
FIELDS=('at','cpu','logical','reads','writes')


def mark(index,suffix):
    names={key:f'@portalProfile_{key}_{suffix}' for key in FIELDS}
    declarations=','.join(value+(' datetime2=SYSUTCDATETIME()' if key=='at' else ' bigint=NULL') for key,value in names.items())
    contexts='\n'.join(f"EXEC sys.sp_set_session_context @key=N'PortalProfile_{key}_{index}',@value={value};" for key,value in names.items())
    return f"""
IF SESSION_CONTEXT(N'PortalProfileRun') IS NOT NULL
BEGIN
 DECLARE {declarations};
 BEGIN TRY
 SELECT {names['cpu']}=CONVERT(bigint,cpu_time),{names['logical']}=logical_reads,
 {names['reads']}=reads,{names['writes']}=writes FROM sys.dm_exec_requests WHERE session_id=@@SPID AND request_id=CURRENT_REQUEST_ID();
 END TRY BEGIN CATCH
 SET {names['cpu']}=NULL;SET {names['logical']}=NULL;SET {names['reads']}=NULL;SET {names['writes']}=NULL;
 END CATCH;
 {contexts}
END;
"""


def begin(run,labels):
    validate(labels)
    resets='\n'.join(f"EXEC sys.sp_set_session_context @key=N'PortalProfile_{key}_{index}',@value=NULL;"
        for index in range(len(labels)+1) for key in FIELDS)
    return f"EXEC sys.sp_set_session_context @key=N'PortalProfileRun',@value={run};\n"+resets+mark(0,'begin')


def finish(labels,*,failed=False):
    validate(labels)
    suffix='failed' if failed else 'completed';last=len(labels)
    rows=[]
    for index,label in enumerate([*labels,'']):
        values=[str(index),"N'"+label.replace("'","''")+"'"]
        values += [f"TRY_CONVERT({'datetime2' if key=='at' else 'bigint'},SESSION_CONTEXT(N'PortalProfile_{key}_{index}'))" for key in FIELDS]
        rows.append('('+','.join(values)+')')
    deltas=','.join(f'CASE WHEN next_{key}>={key} THEN next_{key}-{key} END' for key in FIELDS[1:])
    leads=','.join(f'LEAD({key}) OVER(ORDER BY seq) AS next_{key}' for key in FIELDS)
    state=f"CASE WHEN next_seq={last} THEN N'failed' ELSE N'completed' END" if failed else "N'completed'"
    return mark(last,'end_'+suffix)+f"""
BEGIN TRY
 IF SESSION_CONTEXT(N'PortalProfileRun') IS NOT NULL AND @@TRANCOUNT=0
 BEGIN
 ;WITH snapshots AS (
 SELECT * FROM (VALUES {','.join(rows)}) v(seq,label,at,cpu,logical,reads,writes) WHERE at IS NOT NULL
 ), intervals AS (
 SELECT *,LEAD(seq) OVER(ORDER BY seq) AS next_seq,{leads} FROM snapshots
 )
 INSERT INTO {TABLE}(RunId,Sequence_,Stage_,StartedAt,CompletedAt,ElapsedMs,CpuMs,LogicalReads,Reads_,Writes_,State_)
 SELECT TRY_CONVERT(uniqueidentifier,SESSION_CONTEXT(N'PortalProfileRun')),seq,label,at,next_at,
 DATEDIFF_BIG(MILLISECOND,at,next_at),{deltas},{state}
 FROM intervals WHERE seq<{last} AND next_at IS NOT NULL;
 END;
END TRY BEGIN CATCH
 PRINT N'Не удалось сохранить диагностические замеры этапов; финансовый результат не изменён';
END CATCH;
EXEC sys.sp_set_session_context @key=N'PortalProfileRun',@value=NULL;
"""


def validate(labels):
    if not labels or len(labels)>12 or any(len(label)>150 for label in labels):raise ValueError('Invalid profile stages')


def read(cursor,run_id):
    try:
        cursor.execute(f"""IF OBJECT_ID(N'{TABLE}',N'U') IS NOT NULL
 SELECT Sequence_,Stage_,ElapsedMs,CpuMs,LogicalReads,Reads_,Writes_,State_
 FROM {TABLE} WHERE RunId=? ORDER BY Sequence_;""",str(run_id))
        rows=cursor.fetchall();result=[]
        for row in rows:
            if len(row)!=8:continue
            seq,stage,elapsed,cpu,logical,reads,writes,state=row
            result.append({'sequence':seq,'stage':stage,'seconds':round(elapsed/1000,3),
                'cpu_ms':cpu,'logical_reads':logical,'reads':reads,'writes':writes,'state':state})
        return result
    except Exception:return []
