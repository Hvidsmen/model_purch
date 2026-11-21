from copy import deepcopy
from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .access import can_access
from .sales_planning_access import require as require_planning,check_scenario,scenarios_for,groups_for
from .models import SplitScenario, SalesPlanningChannel
from .sales_planning_scope import split_groups
from .split_forms import ScenarioForm
from .split_stock_overview import overview as stock_overview
from .split_engine import evaluate, planning_months, forecast_months
from .split_workflow import create_scenario, changed_state, persist, approve, correction, forecast_preview
from . import split_exports


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def scenarios(request):
    require_planning(request.user)
    archive_view=request.GET.get('archived')=='1'
    if request.method=='POST' and request.POST.get('action') in ('archive_scenario','restore_scenario'):
        try:
            try:archive_id=int(request.POST.get('scenario_id',''))
            except (ValueError,TypeError):raise ValidationError('Выберите сценарий.')
            selected=get_object_or_404(scenarios_for(request.user,SplitScenario.objects.all()),pk=archive_id)
            from .split_archive import set_archived
            archived=request.POST['action']=='archive_scenario'
            set_archived(selected,request.user,request.POST.get('revision'),archived)
            messages.success(request,'Сценарий помещён в архив.' if archived else 'Сценарий восстановлен из архива.')
        except ValidationError as exc:messages.error(request,' '.join(exc.messages))
        return redirect(reverse('sales_planning_split')+('?archived=1' if archive_view else ''))
    form = ScenarioForm(request.POST if request.method == 'POST' else None,actor=request.user)
    if request.method == 'POST' and form.is_valid():
        from django.db import transaction
        with transaction.atomic():
            scenario = create_scenario(request.user, form.cleaned_data)
            if form.cleaned_data.get('copy_source'):
                from .split_scenario_copy import candidate as copy_candidate
                state,result=copy_candidate(scenario,form.cleaned_data['copy_source'],request.user,'all','replace')
                persist(scenario,request.user,scenario.revision,state,'Создан на основе сценария '+str(form.cleaned_data['copy_source'].pk))
                if result['notes']:messages.warning(request,'Перенос выполнен. Требуют внимания: '+'; '.join(result['notes']))
        return redirect(reverse('split_scenario', args=[scenario.pk]) + '?tab=composition')
    return render(request, 'base/split_scenarios.html', {'form': form,'archive_view':archive_view,
        'page': Paginator(scenarios_for(request.user,SplitScenario.objects.filter(archived_at__isnull=not archive_view)).select_related('created_by').defer('state'),25).get_page(request.GET.get('page'))})


def return_to(pk, tab, gid='', cid='', section='months'):
    from urllib.parse import urlencode
    query = {'tab': tab, 'group': gid, 'channel': cid, 'section':section}
    if tab == 'distribution':
        query['edit'] = '1'
    return reverse('split_scenario', args=[pk]) + '?' + urlencode(query)


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def scenario(request, pk):
    require_planning(request.user)
    item = get_object_or_404(SplitScenario, pk=pk)
    check_scenario(request.user,item)
    if request.method=='POST' and request.POST.get('action') in ('archive_scenario','restore_scenario'):
        try:
            from .split_archive import set_archived
            set_archived(item,request.user,request.POST.get('revision'),request.POST['action']=='archive_scenario')
            return JsonResponse({'ok':True})
        except ValidationError as exc:return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    if request.method=='POST' and request.POST.get('action')=='copy_scenario_step':
        try:
            if item.status!='draft' or str(item.revision)!=str(request.POST.get('revision')):raise ValidationError('Сценарий изменён или утверждён. Обновите страницу.')
            try:source_id=int(request.POST.get('copy_source',''))
            except (ValueError,TypeError):raise ValidationError('Выберите сценарий-источник.')
            source=get_object_or_404(scenarios_for(request.user,SplitScenario.objects.all()),pk=source_id)
            from .split_scenario_copy import candidate as copy_candidate
            state,result=copy_candidate(item,source,request.user,request.POST.get('copy_stage'),request.POST.get('copy_fill','append'))
            persist(item,request.user,request.POST.get('revision'),state,'Скопирован шаг '+request.POST.get('copy_stage','')+' из сценария '+str(source.pk))
            messages.success(request,'Перенесено значений: '+str(result['count']))
            if result['notes']:messages.warning(request,'Требуют внимания: '+'; '.join(result['notes']))
            return JsonResponse({'ok':True,**result})
        except ValidationError as exc:return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    if request.method=='POST' and request.POST.get('action')=='approve' and not can_access(request.user,'sales_planning_admin'):raise PermissionDenied('Утверждение доступно администратору планирования.')
    tab = request.GET.get('tab', 'composition' if not item.state['groups'] or not item.state['channels'] else 'plan')
    if tab not in ('composition', 'plan', 'distribution', 'stock', 'sources', 'checks', 'versions', 'detail'):
        tab = 'plan'
    if tab == 'stock' and request.GET.get('stock_step') == 'supplies':
        tab = 'plan'
    if request.method=='POST' and request.POST.get('action') in ('composition_links_catalog','composition_edit'):
        try:
            if item.status!='draft' or str(item.revision)!=str(request.POST.get('revision')):
                raise ValidationError('Сценарий изменён или утверждён. Обновите страницу.')
            from .split_composition_links import catalog,save as save_composition
            if request.POST['action']=='composition_links_catalog':
                return JsonResponse({'ok':True,**catalog(item,request.user,request.POST.get('link_group',''))})
            save_composition(item,request.user,request.POST)
            if request.headers.get('X-Requested-With')=='XMLHttpRequest':return JsonResponse({'ok':True})
            return redirect(request.get_full_path())
        except ValidationError as exc:return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    if request.method=='POST' and request.POST.get('action') in ('preview_stock_workspace','save_stock_workspace','propose_stock_workspace','calculate_save_stock_workspace','load_stock_workspace_inventory'):
        try:
            if item.status!='draft' or str(item.revision)!=str(request.POST.get('revision')):
                raise ValidationError('Сценарий изменён или утверждён. Обновите страницу.')
            from .split_stock_workspace import candidate,presentation as stock_presentation,serialize,propose,group_ids
            if request.POST['action']=='load_stock_workspace_inventory':
                from .split_inventory import source_rows_many,apply_inventory,model_map
                ids=group_ids(item,request.POST.get('groups','[]'))
                state=deepcopy(item.state);groups={gid:state['groups'][gid] for gid in ids}
                for group in groups.values():model_map(group)
                sources=source_rows_many(item,groups)
                for gid,group in groups.items():apply_inventory(item,group,sources[gid])
                persist(item,request.user,request.POST.get('revision'),state,'Обновлены остатки и поступления · групп: '+str(len(ids)))
                return JsonResponse({'ok':True})
            if request.POST['action']=='calculate_save_stock_workspace':
                import json
                rows=propose(item,request.POST,request.user)
                errors=[row['name']+': '+row.get('error','Нет результата.') for row in rows if not row.get('token') and not row.get('skipped')]
                if errors:raise ValidationError('Ничего не сохранено. '+'; '.join(errors))
                tokens=[row['token'] for row in rows if row.get('token')]
                if tokens:
                    state=candidate(item,'{}',json.dumps(tokens))
                    persist(item,request.user,request.POST.get('revision'),state,'Автоматически рассчитаны и сохранены '+('целевые проценты из закупок' if request.POST.get('kind')=='purchase' else 'новые поставки'))
                return JsonResponse({'ok':True,'count':len(tokens)})
            if request.POST['action']=='propose_stock_workspace':
                return JsonResponse({'ok':True,'rows':propose(item,request.POST,request.user)})
            state=candidate(item,request.POST.get('changes','{}'),request.POST.get('proposal_tokens','[]'),request.POST.get('forecast_tokens','[]'),request.POST.get('forecast_only_empty','1'))
            draft=deepcopy(item);draft.state=state
            report=serialize(stock_presentation(draft))
            if request.POST.get('page_groups') and request.POST['page_groups']!='[]':
                ids=group_ids(item,request.POST['page_groups']);page=deepcopy(draft);page.state['groups']={gid:draft.state['groups'][gid] for gid in ids}
                page_report=serialize(stock_presentation(page));report['page_totals']=page_report['totals'];report['page_counts']=page_report['counts']
            if request.POST['action']=='save_stock_workspace':
                persist(item,request.user,request.POST.get('revision'),state,'Сохранены остатки, целевой запас и поставки')
            return JsonResponse({'ok':True,**report})
        except ValidationError as exc:return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    sales_view='brands' if request.GET.get('sales_view')=='brands' else 'groups'
    if request.method=='POST' and request.POST.get('action') in ('brand_preview','brand_apply'):
        try:
            if item.status!='draft' or str(item.revision)!=str(request.POST.get('revision')):raise ValidationError('Сценарий изменён или утверждён. Обновите страницу.')
            from .split_brand_summary import preview as brand_preview,apply as brand_apply
            if request.POST['action']=='brand_preview':return JsonResponse({'ok':True,**brand_preview(item,request.POST)})
            state=brand_apply(item,request.POST)
            persist(item,request.user,request.POST.get('revision'),state,'Подтверждена корректировка итога бренда')
            return JsonResponse({'ok':True})
        except ValidationError as exc:return JsonResponse({'error':' '.join(exc.messages)},status=400)
    distribution_section = request.GET.get('section', 'months')
    if distribution_section not in ('months','powers'):
        distribution_section = 'months'
    distribution_edit = request.GET.get('edit') == '1' or request.method == 'POST'
    distribution_filters = {key:request.GET.get('filter_'+key,'').strip() for key in ('group','channel','status','q')}
    distribution_channels=[value.strip() for value in request.GET.getlist('filter_channel') if value.strip()]
    distribution_filters['channels']=distribution_channels if distribution_channels or request.GET.get('filter_channels_set')=='1' else None
    distribution_filters['channel_unfiltered']=distribution_filters['channels'] is None
    distribution_filters['positive_only']=request.GET.get('positive_only')=='1'
    gid = request.POST.get('group') or request.GET.get('group') or next(iter(item.state['groups']), '')
    cid = request.POST.get('channel') or request.GET.get('channel') or next(iter(item.state['channels']), '')
    if (gid and gid not in item.state['groups']) or (cid and cid not in item.state['channels']):
        return HttpResponseBadRequest('Выберите группу и канал сценария.')
    if request.method=='POST' and request.POST.get('action') in ('prefill_sales_plan','sales_prefill_versions'):
        try:
            if item.status!='draft' or str(item.revision)!=str(request.POST.get('revision')):
                raise ValidationError('Сценарий изменён или утверждён. Обновите страницу.')
            from .split_sales_prefill import calculate as prefill_sales,versions as sales_versions
            if request.POST['action']=='sales_prefill_versions':return JsonResponse({'ok':True,'versions':sales_versions(item,request.POST)})
            state,result=prefill_sales(item,request.POST)
            if result['count']:persist(item,request.user,request.POST.get('revision'),state,'Предзаполнены продажи и наценки · строк: '+str(result['count']))
            return JsonResponse({'ok':True,**result})
        except ValidationError as exc:return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    if request.method=='POST' and request.POST.get('action') in ('preview_forecast_history','apply_forecast_history','calculate_save_forecast_history','forecast_plan_versions'):
        try:
            if item.status!='draft' or str(item.revision)!=str(request.POST.get('revision')):
                raise ValidationError('Сценарий изменён или утверждён. Обновите страницу.')
            from .split_forecast_history import preview as preview_history,apply as apply_history
            if request.POST['action']=='forecast_plan_versions':
                from .split_forecast_plan import versions
                return JsonResponse({'ok':True,'versions':versions(item)})
            if request.POST['action']=='calculate_save_forecast_history':
                import json
                result=preview_history(item,request.POST)
                rows=[row for row in result['rows'] if not (request.POST.get('only_empty')=='1' and row['has_current'] and not row.get('needs_stock_repair'))]
                skipped=[{'gid':row['gid'],'name':row['name'],'reason':('Прогноз уже заполнен — сохранён без изменений.' if request.POST.get('only_empty')=='1' and row['has_current'] and not row.get('needs_stock_repair') else row.get('error') or 'Нет результата расчёта.')}
                         for row in result['rows'] if (request.POST.get('only_empty')=='1' and row['has_current'] and not row.get('needs_stock_repair')) or not row.get('token')]
                tokens=[row['token'] for row in rows if row.get('token')]
                if tokens:
                    post=request.POST.copy();post['tokens']=json.dumps(tokens)
                    state,count=apply_history(item,post)
                    persist(item,request.user,request.POST.get('revision'),state,'Автоматически рассчитан и сохранён прогноз по истории')
                return JsonResponse({'ok':True,'count':len(tokens),'skipped':skipped,
                    'repairs':[{'name':row['name'],'reason':row['explanation']} for row in rows if row.get('token') and row.get('repaired_existing')]})
            if request.POST['action']=='preview_forecast_history':
                return JsonResponse({'ok':True,**preview_history(item,request.POST)})
            state,count=apply_history(item,request.POST)
            persist(item,request.user,request.POST.get('revision'),state,f'Прогноз до начала плана из аналогичного периода истории · групп: {count}')
            return JsonResponse({'ok':True,'count':count})
        except ValidationError as exc:
            return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    if request.method == 'POST' and request.POST.get('action') in ('ddp_catalog','preview_ddp'):
        try:
            if item.status!='draft' or str(item.revision)!=str(request.POST.get('revision')):
                raise ValidationError('Сценарий изменён или утверждён. Обновите страницу.')
            if not can_access(request.user,'purchases') and (request.POST.get('purchase') or request.POST.get('source')=='purchase'):raise PermissionDenied
            from .split_ddp import catalog,preview
            result=catalog(item,request.POST.get('purchase')) if request.POST['action']=='ddp_catalog' else preview(item,request.POST)
            return JsonResponse({'ok':True,**result})
        except ValidationError as exc:
            return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    if request.method == 'POST' and request.POST.get('action') in ('save_plan_rows','preview_sales_balance'):
        try:
            if item.status != 'draft' or str(item.revision) != str(request.POST.get('revision')):
                raise ValidationError('Расчёт изменён или утверждён. Обновите страницу.')
            from .split_plan_payload import unpack
            post = unpack(request.POST)
            from .split_sales_balance import candidate as sales_candidate, report as sales_balance_report
            state = sales_candidate(item, post, request.POST.get('calculate_supplies') == '1')
            if request.POST['action'] == 'preview_sales_balance':
                return JsonResponse({'ok':True,'rows':sales_balance_report(item,state)})
            persist(item, request.user, request.POST.get('revision'), state, 'Сохранены продажи и новые поставки')
            return JsonResponse({'ok': True})
        except ValidationError as exc:
            return JsonResponse({'ok': False, 'error': ' '.join(exc.messages)}, status=400)
    if request.method == 'POST' and request.POST.get('action') == 'save_stock_rows':
        try:
            if item.status != 'draft' or str(item.revision) != str(request.POST.get('revision')):
                raise ValidationError('Расчёт изменён или утверждён. Обновите страницу.')
            from .split_stock_batch import apply as apply_stock_batch
            state, count = apply_stock_batch(item, request.POST.get('rows', ''))
            persist(item, request.user, request.POST.get('revision'), state, f'Сток и поставки · сохранено групп: {count}')
            return JsonResponse({'ok': True, 'count': count})
        except ValidationError as exc:
            return JsonResponse({'ok': False, 'error': ' '.join(exc.messages)}, status=400)
    if request.method == 'POST' and request.POST.get('action') == 'preview_power_stock_parameters':
        try:
            if item.status != 'draft' or str(item.revision) != str(request.POST.get('revision')):
                raise ValidationError('Сценарий изменён или утверждён. Обновите страницу.')
            from .split_power_stock import preview as preview_stock_parameters
            return JsonResponse({'ok':True,'rows':preview_stock_parameters(item,request.POST.get('rows',''),request.POST.get('only_empty','1'))})
        except ValidationError as exc:return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    if request.method == 'POST' and request.POST.get('action') in ('preview_month_grid','save_month_grid','preview_power_grid','save_power_grid'):
        try:
            if item.status != 'draft' or str(item.revision) != str(request.POST.get('revision')):
                raise ValidationError('Расчёт уже изменён или утверждён. Обновите страницу перед сохранением.')
            power_grid = request.POST['action'] in ('preview_power_grid','save_power_grid')
            if power_grid:
                from .split_power_grid import preview as grid_preview, apply_grid
            else:
                from .split_month_grid import preview as grid_preview, apply_grid
            if request.POST['action'].startswith('preview_'):
                return JsonResponse({'ok':True,**grid_preview(item,gid,cid)})
            state,count = apply_grid(item,request.POST.get('grid_payload',''),allow_incomplete=request.POST.get('save_draft')=='1')
            persist(item,request.user,request.POST.get('revision'),state,('Мощности' if power_grid else 'Месяцы')+f' в таблице · сохранено строк: {count}')
            return JsonResponse({'ok':True,'revision':item.revision})
        except ValidationError as exc:
            return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    error, preview, forecast = '', None, None
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'correction':
                target = correction(item, request.user)
                return redirect('split_scenario', pk=target.pk)
            if action == 'approve':
                approve(item, request.user, request.POST.get('revision'), request.POST.get('acknowledge') == 'on')
            elif action == 'fact_distribution':
                from .split_distribution_history import resolve_proposal, apply_proposal, metadata
                if item.status != 'draft' or str(item.revision) != str(request.POST.get('revision')):
                    raise ValidationError('Обновите черновик перед загрузкой распределения.')
                state = deepcopy(item.state)
                target = state['groups'].get(gid)
                if not target or cid not in target['channels']:
                    raise ValidationError('Выберите группу и канал.')
                values,basis = resolve_proposal(item,target,state['channels'][cid]['name'],distribution_section,refresh=True)
                apply_proposal(target,cid,distribution_section,values)
                target.setdefault('distribution_sources',{})[distribution_section if distribution_section == 'powers' else 'months:'+cid] = metadata(basis)
                persist(item,request.user,request.POST.get('revision'),state,'Распределение из факта за пять лет · '+distribution_section)
            elif action == 'load_inventory_all':
                from .split_inventory import all_inventory_state
                if item.status != 'draft' or str(request.POST.get('revision')) != str(item.revision):
                    raise ValidationError('Обновите черновик перед общей загрузкой остатков и приходов.')
                state=all_inventory_state(item)
                persist(item,request.user,request.POST.get('revision'),state,'Остатки и приходы из StreamGoods · все группы · физические блоки')
            elif action == 'load_inventory':
                from .split_inventory import inventory_state
                if item.status != 'draft' or str(request.POST.get('revision')) != str(item.revision):
                    raise ValidationError('Обновите черновик перед загрузкой остатков и приходов.')
                state=inventory_state(item,gid)
                persist(item,request.user,request.POST.get('revision'),state,'Остатки и приходы из StreamGoods · '+state['groups'][gid]['ref']['name']+' · физические блоки')
            elif action == 'load_history':
                from .split_history import load_history
                if item.status != 'draft' or str(request.POST.get('revision')) != str(item.revision):
                    raise ValidationError('Обновите черновик. Утверждённый исторический факт не перезаписывается.')
                if not item.state['groups'] or not item.state['channels']:
                    raise ValidationError('Сначала добавьте группы и каналы.')
                post = request.POST.copy(); post['action'] = 'lines'
                state = changed_state(item,post) if post.get('with_lines') == '1' else deepcopy(item.state)
                state['history'] = load_history(item)
                persist(item,request.user,request.POST.get('revision'),state,'Загружен исторический факт за три предыдущих календарных года')
            elif action == 'bulk_preview':
                candidate = deepcopy(item)
                candidate.state = changed_state(item, {**request.POST.dict(), 'action': 'bulk'})
                preview = evaluate(candidate)
            elif action in ('forecast_preview', 'forecast_apply'):
                source = get_object_or_404(SplitScenario, pk=request.POST.get('source') if request.POST.get('source', '').isdecimal() else 0, status='approved')
                check_scenario(request.user,source)
                try:
                    start, end = date.fromisoformat(request.POST.get('from', '')), date.fromisoformat(request.POST.get('to', ''))
                except ValueError:
                    raise ValidationError('Укажите даты прогноза.')
                forecast = forecast_preview(item, source, start, end)
                if action == 'forecast_apply':
                    state = deepcopy(item.state)
                    for row in forecast:
                        state['groups'][row['gid']]['forecast_mode']='monthly'
                        state['groups'][row['gid']]['forecast'][row['month']] = str(row['value'])
                        state['groups'][row['gid']].setdefault('forecast_imports', {})[row['month']] = {
                            'start': str(start), 'end': str(end), 'source': source.pk, 'version': source.version}
                    persist(item, request.user, request.POST.get('revision'), state, f'Прогноз из сценария {source.pk}, v{source.version}; {start}–{end}')
            else:
                state = changed_state(item, request.POST)
                if action in ('equal_months','equal_powers'):
                    source_key = 'months:'+cid if action == 'equal_months' else 'powers'
                    state['groups'][gid].get('distribution_sources',{}).pop(source_key,None)
                if action in ('distribution_months','distribution_powers'):
                    scope = 'months' if action == 'distribution_months' else 'powers'
                    source_key = 'months:'+cid if scope == 'months' else 'powers'
                    sources = state['groups'][gid].setdefault('distribution_sources',{})
                    previous_basis = sources.pop(source_key,None)
                    actual = state['groups'][gid]['channels'][cid]['months'] if scope == 'months' else {key:power['share'] for key,power in state['groups'][gid]['powers'].items() if power['active']}
                    token = request.POST.get('basis_token')
                    if token:
                        try:
                            signed = signing.loads(token,salt='split-distribution',max_age=86400)
                            if signed['identity'] != [item.pk,item.revision,gid,cid,scope]:
                                raise signing.BadSignature
                        except (signing.BadSignature,KeyError,TypeError):
                            raise ValidationError('Основание распределения устарело. Обновите страницу.')
                        from decimal import Decimal
                        adjusted = set(actual) != set(signed['shares']) or any(
                            actual[key] is None or Decimal(actual[key]) != Decimal(signed['shares'][key]) for key in actual)
                        adjusted = adjusted or (scope == 'months' and state['groups'][gid]['channels'][cid].get('allocation') != 'percent')
                        sources[source_key] = {**signed['basis'],'adjusted': adjusted}
                    elif previous_basis:
                        old = item.state['groups'][gid]
                        old_shares = old['channels'][cid]['months'] if scope == 'months' else {key:power['share'] for key,power in old['powers'].items() if power['active']}
                        sources[source_key] = {**previous_basis, 'adjusted': previous_basis.get('adjusted',False) or actual != old_shares or
                            (scope == 'months' and state['groups'][gid]['channels'][cid].get('allocation') != old['channels'][cid].get('allocation'))}
                if action == 'bulk' and request.POST.get('confirm') != 'on':
                    raise ValidationError('Подтвердите применение массовой корректировки.')
                persist(item, request.user, request.POST.get('revision'), state,
                    {'group_plan': 'Общий план группы распределён по сохранённым пропорциям каналов', 'lines': 'Объёмы, наценки и DDP', 'distribution': 'Месяцы, мощности и модели', 'distribution_months':'Распределение по месяцам', 'distribution_powers':'Распределение по мощностям', 'stock': 'Остатки, поступления, прогноз и план продаж',
                     'equal_months': 'Равномерные доли месяцев вручную', 'equal_powers': 'Равномерные доли мощностей вручную',
                     'refresh': 'Обновлён снимок состава', 'composition': 'Изменён состав групп и каналов', 'composition_edit': 'Изменён состав групп и каналов', 'bulk': 'Массовая корректировка бренда', 'sources': 'Примечания к источникам',
                     'add_group': 'Добавлена группа в план', 'remove_group': 'Группа исключена из плана',
                     'add_channel': 'Добавлен канал в план', 'remove_channel': 'Канал исключён из плана',
                     'add_onhand_groups': 'Добавлены группы со стока',
                     'add_stock_groups': 'Добавлены группы из стока и товара в пути'}.get((action or '').split(':')[0], action))
            if preview is None and forecast is None or action == 'forecast_apply':
                if action == 'composition' or (action or '').split(':')[0] in ('add_group', 'remove_group', 'add_channel', 'remove_channel', 'add_stock_groups', 'add_onhand_groups'):
                    gid, cid = next(iter(state['groups']), ''), next(iter(state['channels']), '')
                if action in ('add_stock_groups', 'add_onhand_groups'):
                    scope_label = 'Сток' if action == 'add_onhand_groups' else 'Сток и товар в пути'
                    messages.success(request, f"{scope_label} на {item.stock_date:%d.%m.%Y}: найдено групп {len(state['inventory_selection']['groups'])}, добавлено {state['inventory_selection']['added']}. Плановые значения не перезаписаны.")
                else:
                    messages.success(request, 'Изменения сохранены.' if action != 'approve' else 'План утверждён. Редактирование заблокировано.')
                if action == 'composition_edit' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'ok':True,'revision':item.revision})
                target=return_to(pk,tab,gid,cid,distribution_section)
                if request.GET.get('brand_page') and tab in ('composition','plan','stock','distribution'):
                    from urllib.parse import urlencode
                    target+='&'+urlencode({'brand_page':request.GET['brand_page']})
                return redirect(target)
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        except IntegrityError:
            error = 'Версия уже создана в другой вкладке. Обновите страницу.'
    if request.method == 'POST' and request.POST.get('action') == 'composition_edit' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'error':error or 'Не удалось сохранить состав.'},status=400)
    if request.GET.get('export'):
        try:
            if request.GET['export'] == 'xlsx':
                return split_exports.workbook(item)
            if request.GET['export'].startswith('plan_'):
                from .split_sales_exports import workbook as sales_workbook
                return sales_workbook(item,request.GET['export'][5:])
            if request.GET['export'] == 'dwh':
                return split_exports.dwh_file(item)
        except ValidationError as exc:
            error = ' '.join(exc.messages)
    from .split_stock_protection import protected_groups
    stock_locked_groups = protected_groups(item.state)
    data = evaluate(item)
    from .split_brand_summary import report as brand_report
    sales_summary=brand_report(item) if tab=='plan' and sales_view=='brands' else None
    from .split_workspace_ux import presentation as workflow_presentation
    workflow_ux = workflow_presentation(item, data, tab, distribution_section, request.GET.get('stock_step','opening'))
    from .split_history import presentation as history_presentation
    from .split_brand_pages import scope as brand_scope
    display_item,brand_page=brand_scope(item,request.GET) if tab in ('composition','plan','stock','distribution') else (item,None)
    display_data=evaluate(display_item) if display_item is not item else data
    history_projection_requested=request.GET.get('history_forecast',request.COOKIES.get('split-history-forecast','1'))!='0'
    history_projection={'active':False,'issues':[],'note':''}
    if tab=='plan':
        from .split_history_projection import presentation as projected_history
        historical,history_projection=projected_history(item,history_projection_requested)
    else:historical=history_presentation(display_item)
    from .split_matrix_totals import enrich_history, plan_totals, hide_empty_history_years
    hide_empty_history_years(historical)
    historical['latest_year'] = historical['years'][-1] if historical['snapshot'] and historical['years'] else None
    all_history_total = enrich_history(historical)
    if tab == 'plan' and display_item is not item:
        historical['groups'] = [row for row in historical['groups'] if row['gid'] in display_item.state['groups']]
    history_total = enrich_history(historical)
    matrix_totals = plan_totals(display_data, display_item.state)
    all_matrix_totals = plan_totals(data, item.state) if tab == 'plan' and display_item is not item else matrix_totals
    from .split_plan_presentation import presentation as plan_presentation
    plan_detail = plan_presentation(display_item, display_data) if tab == 'plan' else {}
    inventory = stock_overview(display_item, display_data) if tab in ('plan', 'stock') else None
    if tab == 'stock':
        from .split_plan_presentation import references
        stock_references = references(display_item)
        for row in inventory['groups']:
            row['reference'] = stock_references[row['id']]
    from .split_plan_inventory import presentation as inventory_presentation
    plan_inventory = inventory_presentation(inventory) if tab == 'plan' else None
    all_plan_inventory = inventory_presentation(stock_overview(item, data)) if tab == 'plan' and display_item is not item else plan_inventory
    from .split_plan_hierarchy import presentation as hierarchy_presentation
    plan_hierarchy = hierarchy_presentation(display_item, plan_detail, display_data, historical, plan_inventory) if tab == 'plan' else []
    from .split_plan_months import report as plan_months_report
    plan_months=plan_months_report(item,list(display_item.state['groups']),plan_hierarchy) if tab=='plan' and sales_view=='groups' else None
    plan_lines = {}
    for line in data['lines']:
        plan_lines.setdefault(line['gid'], {})[line['cid']] = line
    for historical_group in [*historical['groups'], history_total, all_history_total]:
        for year in historical_group['years']:
            year['by_channel'] = dict(zip(item.state['channels'], year['annual']))
            for month in year['months']:
                month['by_channel'] = dict(zip(item.state['channels'], month['cells']))
    group = item.state['groups'].get(gid, {'ref': {}, 'powers': {}, 'channels': {}, 'forecast': {}})
    basis_token, distribution_basis, distribution_warning = '', None, ''
    source_key = 'months:'+cid if distribution_section == 'months' else 'powers'
    distribution_basis = group.get('distribution_sources',{}).get(source_key)
    selected_line = group['channels'].get(cid, {'months': {}})
    detail_rows = [row for row in data['shipments'] if row['group_key'] == group['ref'].get('source_key') and row['channel_id'] == cid]
    monthly = {}
    from decimal import Decimal
    for row in detail_rows:
        total = monthly.setdefault(row['month'], {'month': row['month'], 'quantity': Decimal(0), 'cost': Decimal(0), 'revenue': Decimal(0)})
        if row['kind'] == 'indoor':
            total['quantity'] += row['quantity']
        total['cost'] += row['cost']
        total['revenue'] += row['revenue']
    months = [{'key': month, 'value': selected_line['months'].get(month),
               'quantity': selected_line.get('month_quantities', {}).get(month)} for month in planning_months(item)]
    powers = []
    for index, (key, power) in enumerate(group['powers'].items()):
        powers.append({**power, 'key': key, 'index': index,
            'indoor_options': [model for model in power['models'] if model['kind'] == 'indoor'],
            'outdoor_options': [model for model in power['models'] if model['kind'] == 'outdoor']})
    comparison = None
    distribution_overview = None
    distribution_steps = {}
    if tab == 'distribution':
        from .split_distribution_overview import overview
        distribution_steps={key:overview(display_item,key,{}) for key in ('months','powers')}
        distribution_overview = overview(display_item,distribution_section,distribution_filters)
        from .split_plan_presentation import references
        distribution_references = references(display_item)
        for row in distribution_overview['rows']:
            row['reference'] = distribution_references[row['gid']]
    from .split_plan_presentation import references as composition_references
    composition_refs=composition_references(display_item) if tab == 'composition' else {}
    composition_rows = [{'id':key,'group':value,'reference':composition_refs.get(key,{})} for key,value in display_item.state['groups'].items()] if tab == 'composition' else []
    if tab == 'composition':
        from .split_stock_protection import snapshot_stock
        for row in composition_rows:
            row['stock_snapshot'] = snapshot_stock(row['group'])
            row['stock_warning'] = row['id'] in stock_locked_groups or row['stock_snapshot']['has_stock']
    reference_filters = None
    if tab == 'plan':
        reference_filters = {gid: detail['reference'] for gid, detail in plan_detail.items()}
    elif tab == 'stock':
        reference_filters = stock_references
    elif tab == 'distribution':
        reference_filters = distribution_references
    card_keys = {group['ref']['source_key'] for group in item.state['groups'].values()}
    card_ids = dict(split_groups().filter(source_key__in=card_keys).values_list('source_key', 'pk'))
    group_card_urls = {gid: reverse('sales_planning_group_detail', args=[card_ids[group['ref']['source_key']]])
                       for gid, group in item.state['groups'].items() if group['ref']['source_key'] in card_ids}
    versions = scenarios_for(request.user,SplitScenario.objects.filter(family=item.family)).order_by('-version')
    compare_id = request.GET.get('compare') or (str(item.parent_id) if item.parent_id else '')
    if compare_id:
        reference = get_object_or_404(versions, pk=compare_id if compare_id.isdecimal() else 0)
        old = evaluate(reference)
        old_lines = {(row['gid'], row['cid']): row for row in old['lines']}
        comparison = {'scenario': reference, 'old': old['totals'], 'new': data['totals'], 'rows': [
            {'new': row, 'old': old_lines.get((row['gid'], row['cid']))} for row in data['lines']]}
    stock_workspace=stock_all_workspace=stock_workspace_data=None
    stock_step=request.GET.get('stock_step','opening')
    if stock_step not in ('opening','targets','supplies'):stock_step='opening'
    stock_filters=[]
    if tab=='stock':
        from .split_stock_workspace import presentation as stock_presentation,serialize
        stock_workspace=stock_presentation(display_item)
        stock_all_workspace=stock_presentation(item) if display_item is not item else stock_workspace
        stock_workspace_data=serialize(stock_all_workspace)
        page_report=serialize(stock_workspace);stock_workspace_data['page_totals']=page_report['totals'];stock_workspace_data['page_counts']=page_report['counts']
        stock_workspace['loaded']=sum(bool(row['group'].get('inventory_source')) for row in stock_workspace['rows'])
        stock_workspace['forecast_filled']=sum(row['opening']['forecast_total'] is not None for row in stock_workspace['rows'])
        for row in stock_workspace['rows']:
            row['reference']=stock_references.get(row['id'],{})
        for name,label in (('sales_plan_group','Группа планов продаж'),('outdoor_type','Тип наружного блока'),('factory','Завод')):
            choices=sorted({str(ref.get(name) or 'Не указано') for ref in stock_references.values()})
            stock_filters.append({'name':name,'label':label,'choices':choices})
    from model_purch.models import ScenarioModel
    from django.utils import timezone
    from zoneinfo import ZoneInfo
    response=render(request, 'base/split_scenario.html', {'ddp_purchase_scenarios':ScenarioModel.objects.order_by('-date_start_plan','-pk') if tab in ('plan','stock') and can_access(request.user,'purchases') else [],
        'stock_workspace':stock_workspace,'stock_all_workspace':stock_all_workspace,'stock_step':stock_step,'stock_workspace_data':stock_workspace_data,'stock_filters':stock_filters,
        'ddp_default_year':min(item.start.year-1,timezone.localdate(timezone=ZoneInfo('Europe/Moscow')).year),
        'copy_scenarios':scenarios_for(request.user,SplitScenario.objects.filter(start__lte=item.start).exclude(pk=item.pk)).only('pk','title','version','stock_date','start').order_by('-pk'),
        'sales_view':sales_view,'sales_summary':sales_summary,'composition_rows':composition_rows, 'all_matrix_totals': all_matrix_totals, 'all_plan_inventory': all_plan_inventory, 'all_history_total': all_history_total,
        'stock_locked_groups': stock_locked_groups, 'workflow_ux': workflow_ux, 'scenario': item, 'state': display_item.state, 'brand_page':brand_page,'data': data, 'tab': tab,
        'needs_composition': not item.state['groups'] or not item.state['channels'],
        'entry_mode': item.state.get('volume_mode', item.entry_mode),
        'history': historical,'history_projection':history_projection,'history_projection_requested':history_projection_requested,
        'history_total': history_total, 'matrix_totals': matrix_totals, 'plan_lines': plan_lines, 'plan_detail': plan_detail,
        'history_by_group': {row['gid']: row for row in historical['groups']},
        'plan_channel_colspan': 1,
        'history_channel_colspan': 1 + (1 if historical['latest_year'] else 0),
        'history_total_colspan': 3 + (1 if historical['latest_year'] else 0),
        'matrix_colspan': 9 + len(item.state['channels']) + ((len(item.state['channels'])+1) if historical['latest_year'] else 0),
        'plan_inventory': plan_inventory,
        'plan_months':plan_months,'group_card_urls': group_card_urls, 'plan_hierarchy': plan_hierarchy, 'reference_filters': reference_filters,
        'error': error, 'preview': preview, 'forecast_preview': forecast, 'post': request.POST,
        'gid': gid, 'cid': cid, 'group': group, 'months': months, 'powers': powers,
        'stock_overview': inventory if tab == 'stock' else None,
        'inventory_focus':request.GET.get('expand','') if request.method == 'GET' else '',
        'distribution_steps':distribution_steps,'distribution_section':distribution_section, 'distribution_basis':distribution_basis,
        'distribution_overview':distribution_overview,'distribution_edit':distribution_edit,'distribution_filters':distribution_filters,
        'basis_token':basis_token, 'distribution_warning':distribution_warning,
        'monthly_results': [monthly[key] for key in sorted(monthly)],
        'detail_page': Paginator(detail_rows, 100).get_page(request.GET.get('page')),
        'history_model_page':Paginator([row for row in (item.state.get('history') or {}).get('model_rows',[]) if row['group']==group['ref'].get('source_key') and row['channel']==str(item.state['channels'].get(cid,{}).get('name','')).strip().casefold()],100).get_page(request.GET.get('fact_page')) if tab=='detail' else None,
        'composition_groups': {**{str(group.pk): {'name': group.name} for group in groups_for(request.user).filter(is_active=True)},
                               **{key: {'name': group['ref']['name']} for key, group in item.state['groups'].items()}},
        'composition_channels': {**{str(channel.pk): {'name': channel.name} for channel in SalesPlanningChannel.objects.filter(is_active=True)}, **item.state['channels']},
        'forecasts': [{'key': month, 'value': group['forecast'].get(month)} for month in forecast_months(item)],
        'brands': sorted({brand for group in item.state['groups'].values() for brand in group['ref']['brand'] if brand}),
        'forecast_sources':scenarios_for(request.user,SplitScenario.objects.filter(status='approved',start__lt=item.start)).order_by('-start'),
        'versions': versions, 'comparison': comparison, 'events': item.events.select_related('actor').order_by('-at')[:100]})
    if request.method=='GET' and request.GET.get('history_forecast') in ('0','1'):
        response.set_cookie('split-history-forecast',request.GET['history_forecast'],max_age=31536000,path='/sales-planning/',samesite='Lax')
    return response
