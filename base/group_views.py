from django import forms
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from admin_motivation.models import Subdivision, TypeCoeff
from .access import can_access, ROLES
from .models import PortalReportGroup


class GroupMembersField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.get_full_name()} ({obj.username})' if obj.get_full_name() else obj.username


class ReportGroupForm(forms.Form):
    name = forms.CharField(label='Название группы', max_length=150, widget=forms.TextInput(attrs={'placeholder': 'Например: Политики · Москва и регионы'}))
    employee_type = forms.ModelChoiceField(label='Вид работника', queryset=TypeCoeff.objects.exclude(type_coeff_name='').order_by('type_coeff_name'), empty_label='Выберите вид работника')
    subdivisions = forms.ModelMultipleChoiceField(label='Подразделения', queryset=Subdivision.objects.order_by('subdivision_key'), widget=forms.CheckboxSelectMultiple)
    members = GroupMembersField(label='Пользователи', queryset=User.objects.order_by('username'), required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, group=None, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)
        if group:
            self.initial['name'] = group.name
            self.initial['members'] = group.user_set.values_list('pk', flat=True)
            scope = getattr(group, 'portal_scope', None)
            if scope:
                self.initial['employee_type'] = scope.employee_type_id
                canonical = {branch.merged_into_id or branch.pk for branch in scope.subdivisions.select_related('merged_into')}
                self.initial['subdivisions'] = canonical

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        duplicate = Group.objects.filter(name__iexact=name)
        if self.group:
            duplicate = duplicate.exclude(pk=self.group.pk)
        if name.casefold() in {role.casefold() for role in ROLES} or duplicate.exists():
            raise forms.ValidationError('Выберите уникальное название, отличное от системных ролей.')
        return name

    @transaction.atomic
    def save(self):
        group = self.group or Group()
        group.name = self.cleaned_data['name']
        group.save()
        scope, _ = PortalReportGroup.objects.update_or_create(group=group, defaults={'employee_type': self.cleaned_data['employee_type']})
        scope.subdivisions.set(self.cleaned_data['subdivisions'])
        group.user_set.set(self.cleaned_data['members'])
        return group


@require_http_methods(['GET', 'POST'])
def groups(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    group = None
    identifier = request.POST.get('group') if request.method == 'POST' else request.GET.get('edit')
    if identifier:
        if not identifier.isdigit():
            raise Http404
        group = get_object_or_404(Group.objects.exclude(name__in=ROLES), pk=int(identifier))
    form = ReportGroupForm(request.POST if request.method == 'POST' else None, group=group)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        messages.success(request, f'Группа «{saved.name}» сохранена. Доступ участников обновлён.')
        return redirect('portal_user_groups')
    catalog = list(Group.objects.exclude(name__in=ROLES).select_related('portal_scope__employee_type').prefetch_related('user_set', 'portal_scope__subdivisions__merged_into').order_by('name'))
    for row in catalog:
        scope = getattr(row, 'portal_scope', None)
        row.reporting_branches = sorted({(branch.merged_into or branch).subdivision_key for branch in scope.subdivisions.all()}) if scope else []
    configured = sum(getattr(row, 'portal_scope', None) is not None for row in catalog)
    return render(request, 'base/user_groups.html', {'form': form, 'editing': group, 'catalog': catalog, 'configured': configured})
