"""Five reports, one independently published scenario and common filter contract."""
from io import BytesIO
from urllib.parse import urlsplit
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse,Http404,HttpResponseBadRequest,JsonResponse,QueryDict
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from django.db import transaction
from .models import ShareholderCalculation
from .shareholder_forms import CalculationForm
from .services import shareholder_reports as reports,shareholder_runner as runner
from .services.shareholder_access import can_start,can_view
from .services.shareholder_scope import scoped_request,current,query as scoped_query,report_kinds
from .services.reports_next_presenter import present,personal_details,new_detail_labels


def allowed(request):
    if not can_view(request.user):raise PermissionDenied


def metadata(run):
    from .services.shareholder_original_rules import enabled
    from .services.shareholder_regions import enabled as regional_enabled
    policy_gate=run.metadata.get('regional_gate_version')=='four-checks-v1'
    regional_meta=[('Подразделения расчёта','Москва + ТТ; Ростов и Питер независимо'),('Планы политиков Ростова/Питера','Доли полного факта 2026 после перераспределения на дату среза'),('Проверка политиков Ростова/Питера','Подразделение + '+('общая база политиков + ' if policy_gate else '')+'общий план сотрудника + формула коэффициента'),('Источник коэффициентов регионов','Утверждённые настройки портала, сохранённые при создании запуска')] if regional_enabled(run) else []
    return [*regional_meta,('Правила расчёта','Исходная схема: годовая база I–IV, доплаты при закрытии года' if enabled(run) else 'Прежняя схема: IV квартал и год по IV кварталу, без доплат'),('Сценарий','Для акционеров · перераспределение реальных отгрузок'),('Запуск',str(run.pk)),
        ('Факт по дату включительно',str(run.as_of)),('Данные','Текущий подготовленный факт на момент запуска'),
        ('Подразделения','Москва + ТТ' if run.scenario=='combined' else 'I–III раздельно; IV и год Москва + ТТ'),
        ('Кварталы определения долей',', '.join(map(str,run.quarters))),('Порог авторов, %',str(run.threshold_percent)),
        ('Плановый курс USD/RUB для прогноза',run.configuration.get('forecast_usd_rub') or 'Не задан'),
        ('План',run.configuration['plan_version']),('Квартальная доля, %',run.configuration['quarter_share']),
        ('Годовая доля, %',run.configuration['year_share']),('Порог выплаты',run.configuration['payout_threshold']),
        ('Подготовленный набор',run.metadata.get('prepared_run','')),('Расчёт завершён',str(run.completed_at))]


@require_http_methods(['GET','POST'])
@scoped_request
def index(request):
    allowed(request)
    form=CalculationForm(request.POST if request.method=='POST' else None)
    if request.method=='POST':
        if not can_start(request.user):raise PermissionDenied
        if form.is_valid():
            try:
                run=runner.create(form.cleaned_data,request.user)
                transaction.on_commit(lambda:runner.launch(run))
                return redirect('motivation_shareholder_report',run.pk,'team')
            except ValidationError as error:form.add_error(None,error)
    archived=request.GET.get('archive')=='1'
    runs=ShareholderCalculation.objects.filter(is_archived=archived).only('id','name','as_of','scenario','threshold_percent','state','stage','created_at').order_by('-created_at','-pk')
    return render(request,'admin_motivation/reports_next/shareholder_index.html',{'form':form,'can_start':can_start(request.user),'skip_motivation_workflow':True,
        'entry_report':'team' if 'team' in report_kinds(request.user) else 'mine','archived':archived,'runs':Paginator(runs,50).get_page(request.GET.get('page'))})


@require_POST
def archive(request,pk):
    allowed(request)
    if not can_start(request.user):raise PermissionDenied
    action=request.POST.get('action')
    if action not in ('archive','restore'):return HttpResponseBadRequest('Неизвестное действие.')
    run=get_object_or_404(ShareholderCalculation.objects.only('pk'),pk=pk)
    # Keep worker state, immutable snapshots and direct report links intact.
    ShareholderCalculation.objects.filter(pk=run.pk).update(is_archived=action=='archive')
    url=reverse('motivation_shareholders')
    return redirect(url+'?archive=1' if request.POST.get('archive')=='1' else url)


@require_GET
@scoped_request
def status(request,pk):
    allowed(request);run=get_object_or_404(ShareholderCalculation.objects.only('state','stage','error'),pk=pk)
    return JsonResponse({'state':run.state,'stage':run.stage,'error':run.error if current.get() is None else ''})


def rewrite_links(value,run):
    """All drilldowns must remain inside the immutable shareholder snapshot."""
    if isinstance(value,list):return [rewrite_links(item,run) for item in value]
    if isinstance(value,dict):return {key:rewrite_links(item,run) for key,item in value.items()}
    if isinstance(value,str) and value.startswith('/') and 'reports-next/' in value and '?' in value:
        parts=urlsplit(value);params=QueryDict(parts.query,mutable=True)
        if '/deals/' in parts.path:params['audit']='1'
        return reverse('motivation_shareholder_report',args=[run.pk,'detail'])+'?'+params.urlencode()+('#'+parts.fragment if parts.fragment else '')
    return value


def plain_excel(title,headers,rows,meta,extra_sheets=()):
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    book=Workbook();sheet=book.active;sheet.title='Отчёт';sheet.append(headers)
    for row in rows:sheet.append(row)
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    for name,columns,values in extra_sheets:
        extra=book.create_sheet(name);extra.append(columns)
        for value in values:extra.append(value)
        extra.freeze_panes='A2';extra.auto_filter.ref=extra.dimensions
    info=book.create_sheet('Источник');info.append([title]);
    for label,value in meta:info.append([label,value])
    for ws in book:
        for line in ws:
            for cell in line:
                if isinstance(cell.value,str):cell.value=ILLEGAL_CHARACTERS_RE.sub('',cell.value);cell.data_type='s'
        for column in ws.columns:ws.column_dimensions[column[0].column_letter].width=min(60,max(16,max(len(str(c.value or '')) for c in column)+2))
    output=BytesIO();book.save(output);book.close();output.seek(0);return output


@require_GET
@scoped_request
def report(request,pk,kind):
    allowed(request)
    if kind not in reports.TITLES:raise Http404
    visible_reports=report_kinds(request.user)
    if kind not in visible_reports:raise PermissionDenied
    run=get_object_or_404(ShareholderCalculation,pk=pk)
    if kind=='deals' and run.state=='completed' and run.metadata.get('closed_deals_version')=='closed-deals-v1':
        from .shareholder_deals_view import report as closed_deals
        return closed_deals(request,run)
    query=request.GET.copy();query['year']='2026'
    if kind=='deals':query.pop('type',None)
    if request.GET.get('export'):query.pop('page',None)
    if kind=='executive':query.pop('author',None);query.pop('search',None)
    for field in ('subdivision','author','planning','goods','brand','type','period'):
        query.setlist(field,[v for v in query.getlist(field) if v])
    query=scoped_query(query)
    if not query.get('currency'):query['currency']='USD'
    try:periods,kinds=reports.selection(run,query)
    except ValidationError as error:return HttpResponseBadRequest(' '.join(error.messages))
    available=reports.options(run) if run.state=='completed' else None
    allowed_authors=set()
    if available is not None:
        selected_branches=set(query.getlist('subdivision'))
        allowed_authors={author for author in available['author']
                         if not selected_branches or selected_branches.intersection(available['author_subdivisions'].get(author,[]))}
        query.setlist('author',[author for author in query.getlist('author')
                               if author not in available['author'] or author in allowed_authors])
    title=reports.TITLES[kind];meta=metadata(run)
    if current.get() is not None:
        from .services.reports_next import available as main_catalog
        title=main_catalog(request.user).get(kind,(title,''))[0]
        if run.state=='failed':run.error='Расчёт не завершён. Обратитесь к администратору.'
    meta.extend((label,', '.join(query.getlist(field)) or 'Все доступные') for field,label in [('currency','Валюта премий'),('period','Периоды отчёта'),('type','Виды мотивации'),('subdivision','Отбор подразделений'),('planning','Группа планов продаж'),('goods','Группа товаров'),('brand','Бренд')])
    from .services.shareholder_original_rules import enabled,enrich
    from .services.shareholder_regions import enabled as regional_enabled
    context={'shareholder_policy_gate':run.metadata.get('regional_gate_version')=='four-checks-v1','shareholder_regional_rules':regional_enabled(run),'shareholder_original_rules':enabled(run),'skip_motivation_workflow':True,'shareholder_report':True,'run':run,'kind':kind,'title':title,'query':query,'metadata':meta,'pending':run.state in ('queued','running'),
        'status_url':reverse('motivation_shareholder_status',args=[run.pk]),'selected_types':kinds,
        'period_options':[{'code':code,'label':'Год' if code=='5' else code+' квартал','selected':code in periods} for code in [*map(str,run.quarters),'5']]}
    navigation=[]
    for key,label in reports.TITLES.items():
        if key not in visible_reports:continue
        target=query.copy();target.pop('export',None);target.pop('audit',None);target.pop('page',None)
        if key=='executive':target.pop('author',None);target.pop('search',None)
        navigation.append({'title':label,'active':key==kind,'url':reverse('motivation_shareholder_report',args=[run.pk,key])+'?'+target.urlencode()})
    context['navigation']=navigation
    if run.state=='completed':
        context['filters']=[{'key':key,'label':label,'options':[{'value':v,'selected':v in query.getlist(key)} for v in available[key]]} for key,label in [('subdivision','Подразделения'),('planning','Группа планов продаж'),('goods','Группа товаров'),('brand','Бренд')]]
        context['authors']=[{'value':v,'selected':v in query.getlist('author'),'disabled':v not in allowed_authors} for v in available['author']]
        context['author_subdivisions']=available['author_subdivisions']
        context['type_options']=[{'value':v,'selected':v in kinds} for v in (current.get().kinds if current.get() else reports.KINDS)]
        context['needs_author']=kind=='mine' and not query.getlist('author')
        context['multiple_authors']=kind=='mine' and len(query.getlist('author'))>1
        def caption(values,all_caption):
            return ', '.join(values) if values and len(values)<=2 else f'Выбрано: {len(values)}' if values else all_caption
        context.update(branches=[{'name':item['value'],'selected':item['selected']} for item in context['filters'][0]['options']],
            author_label=caption(query.getlist('author'),'Выберите сотрудников'),
            branch_label=caption(query.getlist('subdivision'),'Все доступные подразделения'),
            type_choices=context['type_options'],type_label=caption(query.getlist('type'),'Все доступные виды'),
            period_choices=[{'value':item['code'],'label':item['label'],'selected':item['selected']} for item in context['period_options']],
            period_label='Все доступные периоды' if len(periods)==len(context['period_options']) else ', '.join(item['label'] for item in context['period_options'] if item['selected']),
            product_filter_fields=[{'name':item['key'],'label':item['label'],'caption':caption(query.getlist(item['key']),'Все доступные'),'options':item['options']} for item in context['filters'][1:]])
        if kind not in ('mine','executive'):
            context['product_filter_fields'].append({'name':'author','label':'Сотрудники','caption':caption(query.getlist('author'),'Все сотрудники'),'options':context['authors']})

        if kind=='deals':
            context['legacy_deals']=True
            from decimal import Decimal
            rows=list(reports.audit_rows(run,query))
            context.update(deals_page=Paginator(rows,50).get_page(query.get('page',1)),
                deals_usd=sum((Decimal(str(row.get('AllocatedUSD') or 0)) for row in rows),Decimal(0)),
                deals_rub=sum((Decimal(str(row.get('AllocatedRUB') or 0)) for row in rows),Decimal(0)),
                ledger={'currency':query['currency']},page_query=dict_query(query).urlencode(),
                export_url='?'+dict_query(query,export='xlsx').urlencode())
            if query.get('export')=='xlsx':
                output=plain_excel(title,['Дата','Подразделение источника','Документ','Код товара','Исходный автор','Получатель','Доля','Оборот USD','Оборот RUB'],
                    [[row['ClosedDate'],row['SourceBranch'],row['DocumentNumber'],row['ProductCode'],row['OriginalAuthor'],row['AssignedAuthor'],Decimal(str(row['Weight_'])),Decimal(str(row['AllocatedUSD'])),Decimal(str(row['AllocatedRUB']))] for row in rows],meta)
                return FileResponse(output,as_attachment=True,filename=f'shareholder-deals-{run.pk}.xlsx')
            return render(request,'admin_motivation/reports_next/shareholder_report.html',context)

        if not context['needs_author']:
            data=reports.data_for(run,query,detailed=kind in ('mine','detail'),defer_detail=kind=='detail')
            display=present('team' if kind=='team' else 'registry',data,data,periods)
            enrich(display,data)
            for section in display['team_sections']:section['page']=Paginator(section['rows'],50).get_page(query.get(section['page_parameter'],1))
            context.update(ledger=data,presentation=display,page_query=query.urlencode(),
                employee_page=Paginator(display['employees'],50).get_page(query.get('page',1)),ledger_page=Paginator(display['registry_rows'],50).get_page(query.get('page',1)))
            if kind=='mine':
                display.update(rewrite_links(personal_details(data,query,period_codes=periods),run))
            if kind=='detail':
                from .services.manager_detail import combine_reports
                detail=new_detail_labels(combine_reports(data['sections'],data['currency']))
                from .services.detail_summary_breakdown import enrich as enrich_detail_summary
                enrich_detail_summary(detail,data,display)
                from .services.extended_premium_forecast import enrich as enrich_extended_forecast
                enrich_extended_forecast(detail,data)
                from .services.detail_execution import enrich as enrich_execution
                enrich_execution(detail,data)
                detail['excel_urls']={name:'?'+dict_query(query,export='xlsx',table=name).urlencode() for name in ('all','summary','detail')}
                context['data']=detail
            macro=reports.macro_rows(data,periods);context['macro_rows']=macro
            from .services.reports_next_presenter import performance_matrix
            global_bases=[r for r in data['plan_fact'] if r['formula']=='Подразделение(Глобально)']
            context['macro_bases']=performance_matrix(global_bases,display['period_columns'])
            context['macro_periods']=[('Год' if code=='5' else code+' квартал') for code in periods]
            audit=kind=='detail' and query.get('audit')=='1';context['audit']=audit
            if audit:context['audit_page']=Paginator(list(reports.audit_rows(run,query)),50).get_page(query.get('page',1))
            if query.get('export')=='xlsx':
                if audit:
                    from decimal import Decimal
                    trace=list(reports.audit_rows(run,query))
                    output=plain_excel('Исходные авторы и перераспределение',['Дата','Подразделение','Документ','Товар','Исходный автор','Получатель','Доля','USD','RUB'],
                        [[r['ClosedDate'],r['SourceBranch'],r['DocumentNumber'],r['ProductCode'],r['OriginalAuthor'],r['AssignedAuthor'],Decimal(str(r['Weight_'])),Decimal(str(r['AllocatedUSD'])),Decimal(str(r['AllocatedRUB']))] for r in trace],meta)
                elif kind=='executive':
                    output=plain_excel(title,['Подразделение','Мотивация',*[f'{label}: {metric}' for label in context['macro_periods'] for metric in ('Возможная премия','Премия по расчёту')],'Возможная премия, всего','Всего по расчёту'],
                        [[r['subdivision'],r['kind'],*[v for cell in r['cells'] for v in (cell['possible'],cell['total'])],r['possible'],r['total']] for r in macro],meta,extra_sheets=[('Базы выполнения',['Подразделение','Вид базы','Период','План USD','Факт USD','Выполнение'],
                            [[r['subdivision'],r['kind'],r['period'],r['plan'],r['fact'],r['ratio']] for r in { (r['subdivision'],r['kind'],r['period']):r for r in [*global_bases,*data['department_plan_fact']]}.values()])])
                elif kind=='detail':
                    from .services.report_excel import report_excel
                    table=query.get('table','all')
                    if table not in ('all','summary','detail'):return HttpResponseBadRequest('Неизвестная таблица.')
                    output=report_excel(context['data'],{'Currency':data['currency'],'Years':['2026'],'TypeCoeff_':kinds},table,provenance=meta)
                else:
                    from .services.presentation_excel import workbook
                    output=workbook('team' if kind=='team' else 'registry',data,meta,query,title)
                if enabled(run) and not audit:
                    output=catchup_excel(output,data,run)
                return FileResponse(output,as_attachment=True,filename=f'shareholders-{run.pk}-{kind}.xlsx')
    context['export_url']='?'+dict_query(query,export='xlsx').urlencode()
    context['audit_url']=reverse('motivation_shareholder_report',args=[run.pk,'detail'])+'?'+dict_query(query,audit='1').urlencode()
    context['detail_url']='?'+dict_query(query,audit='').urlencode()
    return render(request,'admin_motivation/reports_next/shareholder_report.html',context)


def dict_query(query,**values):
    result=query.copy()
    for key,value in values.items():result[key]=value
    return result


def catchup_excel(output, ledger, run=None):
    """Identical breakdown for all new-scheme exports; potential is never a payout."""
    from openpyxl import load_workbook
    book=load_workbook(output)
    output.close()
    from .services.reports import PILOT_BONUS_NOTE
    from openpyxl.comments import Comment
    note='Исходная схема акционеров: невыплаченные квартальные премии доплачиваются при выполнении годового условия I–IV кварталов. Возможные доплаты в итог не включены.'
    for existing in book:
        for line in existing:
            for cell in line:
                if cell.value==PILOT_BONUS_NOTE:cell.value=note
                if cell.comment and cell.comment.text==PILOT_BONUS_NOTE:cell.comment=Comment(note,'Портал')
    sheet=book.create_sheet('Доплаты и годовая часть')
    sheet.append(['Подразделение','Сотрудник','Вид мотивации','Период','Валюта премии',
        'Возможная премия','Премия по расчёту','В годовую премию',
        'Возможная доплата за кварталы','Доплата к выплате','Всего к выплате'])
    for row in ledger['rows']:
        sheet.append([row['subdivision'],row['author'],row['kind'],row['period'],ledger['currency'],
            row['possible'],row['amount'],row['annual_accrual'],row['potential_bonus'],row['bonus'],row['total']])
    sheet.freeze_panes='F2';sheet.auto_filter.ref=sheet.dimensions
    for line in sheet.iter_rows(min_row=2):
        for cell in line:
            if isinstance(cell.value,str):cell.data_type='s'
            elif cell.column>=6:cell.number_format='#,##0.00'
    for column in sheet.columns:sheet.column_dimensions[column[0].column_letter].width=28
    info=book.create_sheet('Правила доплат')
    info.append(['Возможная доплата','Невыплаченная квартальная премия; не входит в итог к выплате.'])
    info.append(['Годовая строка','Возможные доплаты кварталов свёрнуты в годовой строке: повторно не складывать.'])
    info.append(['Доплата к выплате','Только при выполнении годового условия I–IV кварталов.'])
    info.append(['В годовую премию','Отдельные годовые доли кварталов; не входят в квартальные доплаты.'])
    if run is not None and run.configuration.get('regional_rule'):
        selected={row['subdivision'] for row in ledger['rows']}
        shares=book.create_sheet('Доли плана регионов');shares.append(['Подразделение','Автор','Факт после перераспределения, USD','Доля плана'])
        for row in reports.records(run,'policy_plan_shares'):
            if row['Subdivision'] in selected:shares.append([row['Subdivision'],row['Author_'],float(row['Turnover_']),float(row['Weight_'])])
        rates=book.create_sheet('Коэффициенты регионов');columns=['Subdivision','DateFrom','DateTo','PGSales','Group_','Brand','TypeCoeff','K0','K1','K2','K3','K4','VariationCalculate'];rates.append(columns)
        for row in reports.records(run,'policy_rates'):
            if row['Subdivision'] in selected:rates.append([float(row[c]) if c.startswith('K') else row[c] for c in columns])
        defaults=book.create_sheet('Формулы по умолчанию');defaults.append(['Действует с','Формула при отсутствии точной настройки'])
        for row in reports.records(run,'policy_defaults'):defaults.append([row['Date_'],row['DefaultFormula']])
        for sheet_ in (shares,rates,defaults):
            sheet_.freeze_panes='A2';sheet_.auto_filter.ref=sheet_.dimensions
            for line in sheet_.iter_rows(min_row=2):
                for cell in line:
                    if isinstance(cell.value,str):cell.data_type='s'
                    if sheet_==shares and cell.column==4:cell.number_format='0.0000%'
                    if sheet_==rates and 8<=cell.column<=12:cell.number_format='0.0000%'
    result=BytesIO();book.save(result);book.close();result.seek(0);return result
