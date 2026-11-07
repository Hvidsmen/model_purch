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
from .sales_planning_group_service import rebuild_groups, ungrouped_goods, group_summaries, group_goods, save_pair, pair_error


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
    page = Paginator(queryset, 25).get_page(request.GET.get('page'))
    return render(request, 'base/sales_planning_groups.html', {
        'error': error, 'page': page, 'rows': group_summaries(list(page)), 'search': search,
        'include_inactive': request.GET.get('inactive') == '1',
        'load': SalesPlanningDirectoryLoad.objects.filter(directory='groups').first(),
        'ungrouped_count': ungrouped_goods().count(),
        'ungrouped_page': Paginator(ungrouped_goods(), 25).get_page(request.GET.get('missing_page')),
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
        elif action == 'remove_pair':
            group.block_pairs.filter(pk=request.POST.get('pair') if request.POST.get('pair', '').isdecimal() else 0).delete()
            return redirect('sales_planning_group_detail', pk=pk)
        else:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
    goods = list(group_goods(group))
    powers = {}
    for good in goods:
        power = good.power_class.strip()
        if not power:
            continue
        row = powers.setdefault(power, {'power': power, 'indoor': [], 'outdoor': []})
        block = good.block_type.strip().casefold()
        if block.startswith('внутр'):
            row['indoor'].append(good)
        elif block.startswith('наруж'):
            row['outdoor'].append(good)
    for pair in group.block_pairs.select_related('indoor', 'outdoor', 'group'):
        row = powers.setdefault(pair.power, {'power': pair.power, 'indoor': [], 'outdoor': []})
        row['pair'], row['error'] = pair, pair_error(pair)
    return render(request, 'base/sales_planning_group_detail.html', {
        'group': group, 'summary': group_summaries([group])[0], 'form': form, 'error': error,
        'powers': [powers[key] for key in sorted(powers)],
        'goods_page': Paginator(goods, 50).get_page(request.GET.get('page')),
        'unclassified': sum(not good.power_class.strip() or not good.block_type.strip().casefold().startswith(('внутр', 'наруж')) for good in goods),
    })
