"""Read compact frozen export data without decoding million-row run snapshots."""
import json
from types import SimpleNamespace
from django.core.cache import cache
from django.db import connection
from ..models import CoefficientIterationRun
from .coefficient_iteration_runner import SCENARIOS
from .coefficient_iteration_export_summary import summarize_plan_facts

FIELDS=('branch','kind','date','period','original_branch','planning','goods','brand','formula','department_plan','department_fact','plan','fact','segments')


def json_path(parts):return '$'+''.join('.'+json.dumps(str(part),ensure_ascii=False) for part in parts)


def project(run_id,field,paths):
    if connection.vendor!='sqlite':
        root=CoefficientIterationRun.objects.values_list(field,flat=True).get(pk=run_id)
        result=[]
        for parts in paths:
            value=root
            for part in parts:value=value.get(str(part)) if isinstance(value,dict) else None
            result.append(value)
        return result
    table=connection.ops.quote_name(CoefficientIterationRun._meta.db_table)
    column=connection.ops.quote_name(CoefficientIterationRun._meta.get_field(field).column)
    # A single multipath extraction parses a large JSON column only once.
    args=['$.__export_unused__',*[json_path(parts) for parts in paths]]
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT json_extract({column},'+','.join(['%s']*len(args))+f') FROM {table} WHERE id=%s',[*args,run_id])
        value=cursor.fetchone()[0]
    return json.loads(value)[1:] if value is not None else [None]*len(paths)


def array_records(run_id,field,parts,fields):
    if connection.vendor!='sqlite':
        rows=project(run_id,field,[parts])[0] or []
        for row in rows:
            if isinstance(row,dict):yield {key:row.get(key) for key in fields}
        return
    table=connection.ops.quote_name(CoefficientIterationRun._meta.db_table)
    column=connection.ops.quote_name(CoefficientIterationRun._meta.get_field(field).column)
    args=[json_path([key]) for key in fields]
    with connection.cursor() as cursor:
        cursor.execute('SELECT json_extract(j.value,'+','.join(['%s']*len(args))+f') FROM {table} r, json_each(r.{column},%s) j WHERE r.id=%s',
                       [*args,json_path(parts),run_id])
        while True:
            rows=cursor.fetchmany(1000)
            if not rows:break
            for value, in rows:
                if value is not None:yield dict(zip(fields,json.loads(value)))


def export_snapshot(run):
    stamp=run.completed_at or run.started_at
    key=f'iteration-compact-export-v1:{run.pk}:{run.state}:{stamp.isoformat() if stamp else ""}'
    data=cache.get(key)
    if data is None:
        paths=[['rule'],['revision'],['excluded_subdivision_names'],['subdivision_policy'],['coefficient_exclusions'],
               ['subdivisions'],['subdivision_names'],['formulas','version_name']]
        paths += [['payload',source,field] for source in ('branches','directors') for field in ('name','sha256')]
        paths += [['plans',str(year),field] for year in (2025,2026,2027) for field in ('id','title','source','version','loaded_at','sha256')]
        values=iter(project(run.pk,'inputs',paths))
        inputs={name:next(values) for name in ('rule','revision','excluded_subdivision_names','subdivision_policy','coefficient_exclusions')}
        subdivisions,names=next(values) or {},next(values) or {}
        inputs['export_names']={str(code):names.get(str(pk),names.get(pk,str(code))) for pk,code in subdivisions.items()}
        inputs['formulas']={'version_name':next(values) or ''}
        inputs['payload']={source:{field:next(values) or '' for field in ('name','sha256')} for source in ('branches','directors')}
        inputs['plans']={}
        for year in (2025,2026,2027):
            plan={field:next(values) for field in ('id','title','source','version','loaded_at','sha256')}
            if plan['id'] is not None or plan['title'] is not None:inputs['plans'][str(year)]=plan
        paths=[]
        for scenario in SCENARIOS:
            paths.extend([[scenario,field] for field in ('state','title','year','rows','error','plan_fact_summary')])
            paths.extend([[scenario,'source',field] for field in ('source','prepared_run_id','completed_at')])
        values=iter(project(run.pk,'results',paths));results={}
        for scenario,(year,title) in SCENARIOS.items():
            item={field:next(values) for field in ('state','title','year','rows','error','plan_fact_summary')}
            item['source']={field:next(values) for field in ('source','prepared_run_id','completed_at')}
            if item['state'] is None:continue
            item['year']=item['year'] or year;item['title']=item['title'] or title
            if item['state']=='completed' and item['plan_fact_summary'] is None:
                item['plan_fact_summary']=summarize_plan_facts(array_records(run.pk,'results',[scenario,'details'],FIELDS),int(item['year']))
            results[scenario]=item
        # Legacy snapshots may contain only physical-key/name pairs in plan rows.
        if not inputs['export_names']:
            for year in (2025,2026,2027):
                for row in array_records(run.pk,'inputs',['plans',str(year),'rows'],('branch','branch_name')):
                    if row.get('branch'):inputs['export_names'][row['branch']]=row.get('branch_name') or row['branch']
        data={'inputs':inputs,'results':results}
        cache.set(key,data,3600)
    return SimpleNamespace(pk=run.pk,iteration_id=run.iteration_id,state=run.state,configuration=run.configuration,
                           started_at=run.started_at,completed_at=run.completed_at,error=run.error,**data)
