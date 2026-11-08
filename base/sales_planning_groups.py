from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .access import can_access
from .models import SalesPlanningGroup, SalesPlanningDirectoryLoad
from .sales_planning_group_service import rebuild_groups, ungrouped_goods, group_summaries, group_goods, save_pair, block_kind, power_rows, save_unique_pairs
from .sales_planning_group_filters import filter_groups, page_link, STATUSES, ATTRIBUTES


class GroupSettingsForm(forms.ModelForm):
    class Meta:
        model = SalesPlanningGroup
        fields = ['notes', 'related_groups', 'replacement_groups']
        labels = {'related_groups': 'Связанные группы', 'replacement_groups': 'Замещающие группы'}
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        selected = list(self.instance.related_groups.values_list('pk', flat=True)) + list(self.instance.replacement_groups.values_list('pk', flat=True))
        for field in ('related_groups', 'replacement_groups'):
            self.fields[field].queryset = SalesPlanningGroup.objects.filter(Q(is_active=True) | Q(pk__in=selected)).exclude(pk=self.instance.pk)
            self.fields[field].help_text = 'Выбор нескольких групп: Ctrl / Cmd. Неактивные сохранённые связи не удаляются при обновлении.'


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def groups(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    error = ''
    if request.method == 'POST':
        if request.POST.get('action') != 'rebuild':
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
        try:
            count = rebuild_groups()
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        else:
            messages.success(request, f'Группы планирования обновлены: {count}. Ручные настройки сохранены.')
            return redirect('sales_planning_groups')
    search = request.GET.get('q', '').strip()[:255]
    queryset = SalesPlanningGroup.objects.all()
    if request.GET.get('inactive') != '1':
        queryset = queryset.filter(is_active=True)
    if search:
        queryset = queryset.filter(name__icontains=search)
    try:
        queryset, filters, statuses, selected_filters = filter_groups(queryset, request.GET)
    except ValidationError as exc:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest(' '.join(exc.messages))
    page = Paginator(queryset, 25).get_page(request.GET.get('page'))
    rows = group_summaries(list(page))
    for row in rows:
        row['pair_status'] = dict(STATUSES)[statuses.get(row['group'].source_key, 'pending')]
    missing = ungrouped_goods()
    for field, label in ATTRIBUTES:
        if selected_filters[field]:
            missing = missing.filter(**{field + '__in': selected_filters[field]})
    missing_page = Paginator(missing, 25).get_page(request.GET.get('missing_page'))
    return render(request, 'base/sales_planning_groups.html', {
        'error': error, 'page': page, 'rows': rows, 'search': search, 'filters': filters,
        'previous_url': page_link(request.GET, 'page', page.previous_page_number()) if page.has_previous() else '',
        'next_url': page_link(request.GET, 'page', page.next_page_number()) if page.has_next() else '',
        'missing_previous_url': page_link(request.GET, 'missing_page', missing_page.previous_page_number()) if missing_page.has_previous() else '',
        'missing_next_url': page_link(request.GET, 'missing_page', missing_page.next_page_number()) if missing_page.has_next() else '',
        'include_inactive': request.GET.get('inactive') == '1',
        'load': SalesPlanningDirectoryLoad.objects.filter(directory='groups').first(),
        'ungrouped_count': missing_page.paginator.count,
        'ungrouped_page': missing_page,
    })


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def detail(request, pk):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    group = get_object_or_404(SalesPlanningGroup, pk=pk)
    error = ''
    form = GroupSettingsForm(instance=group)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'settings':
            form = GroupSettingsForm(request.POST, instance=group)
            if form.is_valid():
                form.save()
                messages.success(request, 'Настройки Split сохранены.')
                return redirect('sales_planning_group_detail', pk=pk)
        elif action == 'pair':
            try:
                indoor, outdoor = request.POST.get('indoor', ''), request.POST.get('outdoor', '')
                if not indoor.isdecimal() or not outdoor.isdecimal():
                    raise ValidationError('Выберите внутренний и наружный блоки.')
                save_pair(group, request.POST.get('power', ''), indoor, outdoor)
            except ValidationError as exc:
                error = ' '.join(exc.messages)
            else:
                messages.success(request, 'Пара блоков сохранена.')
                return redirect('sales_planning_group_detail', pk=pk)
        elif action == 'auto_pairs':
            try:
                count = save_unique_pairs(group)
            except ValidationError as exc:
                error = ' '.join(exc.messages)
            else:
                messages.success(request, f'Сохранено однозначных пар: {count}. Существующие пары не изменены.')
                return redirect('sales_planning_group_detail', pk=pk)
        elif action == 'remove_pair':
            group.block_pairs.filter(pk=request.POST.get('pair') if request.POST.get('pair', '').isdecimal() else 0).delete()
            return redirect('sales_planning_group_detail', pk=pk)
        else:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
    goods = list(group_goods(group))
    powers = power_rows(group, goods)
    return render(request, 'base/sales_planning_group_detail.html', {
        'group': group, 'summary': group_summaries([group])[0], 'form': form, 'error': error,
        'powers': powers,
        'goods_page': Paginator(goods, 50).get_page(request.GET.get('page')),
        'unclassified': sum(not good.power_class.strip() or not block_kind(good.block_type) for good in goods),
    })


