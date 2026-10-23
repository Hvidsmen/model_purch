from django import forms
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from admin_motivation.models import Subdivision, TypeCoeff
from .access import can_access, ROLES, ADMIN, POLITICIAN, SALES, DEPUTY_DIRECTOR, REPORT_ROLES, PERSONAL_ROLES, role_for
from .models import PortalReportGroup, MotivationReportAccess


class GroupMembersField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.get_full_name()} ({obj.username})' if obj.get_full_name() else obj.username


class ReportGroupForm(forms.Form):
    name = forms.CharField(label='Название группы', max_length=150, widget=forms.TextInput(attrs={'placeholder': 'Например: Политики · Москва и регионы'}))
    portal_role = forms.ChoiceField(label='Роль в портале', choices=[('', 'Выберите роль')] + [(role, role) for role in ROLES])
    employee_type = forms.ModelChoiceField(label='Вид работника', queryset=TypeCoeff.objects.exclude(type_coeff_name='').order_by('type_coeff_name'), empty_label='Выберите вид работника', required=False, widget=forms.HiddenInput)
    subdivisions = forms.ModelMultipleChoiceField(label='Подразделения', queryset=Subdivision.objects.order_by('subdivision_key'), required=False, widget=forms.CheckboxSelectMultiple)
    members = GroupMembersField(label='Пользователи', queryset=User.objects.order_by('username'), required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, group=None, **kwargs):
        self.group = group
        self.legacy_kind = bool(args and args[0] is not None and 'portal_role' not in args[0] and args[0].get('employee_type'))
        # Accept old clients submitting the report kind; new forms submit the role.
        if args and args[0] is not None and 'portal_role' not in args[0] and args[0].get('employee_type'):
            data = args[0].copy()
            data['portal_role'] = POLITICIAN
            args = (data, *args[1:])
        super().__init__(*args, **kwargs)
        if group:
            self.initial['name'] = group.name
            self.initial['members'] = group.user_set.values_list('pk', flat=True)
            scope = getattr(group, 'portal_scope', None)
            if scope:
                self.initial['employee_type'] = scope.employee_type_id
                self.initial['portal_role'] = scope.portal_role
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

    def clean(self):
        data = super().clean()
        role = data.get('portal_role')
        if role in REPORT_ROLES and not data.get('subdivisions'):
            self.add_error('subdivisions', 'Выберите хотя бы одно подразделение для отчётов.')
        return data

    @transaction.atomic
    def save(self):
        group = self.group or Group()
        group.name = self.cleaned_data['name']
        group.save()
        previous = getattr(group, 'portal_scope', None)
        old_role = previous.portal_role if previous else None
        role = self.cleaned_data['portal_role']
        worker = self.cleaned_data.get('employee_type') if self.legacy_kind else None
        if not self.legacy_kind and previous and previous.portal_role == role:
            worker = previous.employee_type
        worker_name = 'Продажи' if role == SALES else 'Политики' if role in (POLITICIAN, DEPUTY_DIRECTOR) else None
        if worker is None and worker_name:
            worker = TypeCoeff.objects.filter(type_coeff_name=worker_name).order_by('pk').first()
        scope, _ = PortalReportGroup.objects.update_or_create(group=group, defaults={'employee_type': worker, 'portal_role': role})
        if role == ADMIN:
            from django.contrib.auth.models import Permission
            group.permissions.set(Permission.objects.all())
        elif old_role == ADMIN:
            group.permissions.clear()
        affected = set(group.user_set.values_list('pk', flat=True)) | {user.pk for user in self.cleaned_data['members']}
        scope.subdivisions.set(self.cleaned_data['subdivisions'])
        group.user_set.set(self.cleaned_data['members'])
        for user in User.objects.filter(pk__in=affected):
            effective = role_for(user)
            user.is_staff = effective == ADMIN
            user.save(update_fields=['is_staff'])
            if effective in PERSONAL_ROLES and user.first_name.strip() and user.last_name.strip():
                branch = scope.subdivisions.order_by('subdivision_key').first()
                if branch and group.user_set.filter(pk=user.pk).exists():
                    MotivationReportAccess.objects.get_or_create(user=user, defaults={
                        'subdivision': branch, 'deal_author': ' '.join(f'{user.last_name} {user.first_name}'.split())})
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
