from .project_catalog import selector, group_roles
from . import project_access_forms as project_forms
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
    motivation_role = project_forms.project_field('motivation')
    purchases_role = project_forms.project_field('purchases')
    dashboards_role = project_forms.project_field('dashboards')
    portal_role = forms.ChoiceField(label='Роль в портале', required=False, choices=[('', 'Выберите роль')] + [(role, role) for role in ROLES])
    employee_type = forms.ModelChoiceField(label='Вид работника', queryset=TypeCoeff.objects.exclude(type_coeff_name='').order_by('type_coeff_name'), empty_label='Выберите вид работника', required=False, widget=forms.HiddenInput)
    subdivisions = forms.ModelMultipleChoiceField(label='Подразделения', queryset=Subdivision.objects.order_by('subdivision_key'), required=False, widget=forms.CheckboxSelectMultiple)
    members = GroupMembersField(label='Пользователи', queryset=User.objects.order_by('username'), required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, group=None, actor=None, **kwargs):
        self.group = group
        self.actor = actor
        self.legacy_kind = bool(args and args[0] is not None and 'portal_role' not in args[0] and args[0].get('employee_type'))
        # Accept old clients submitting the report kind; new forms submit the role.
        if args and args[0] is not None and 'portal_role' not in args[0] and args[0].get('employee_type'):
            data = args[0].copy()
            data['portal_role'] = POLITICIAN
            args = (data, *args[1:])
        super().__init__(*args, **kwargs)
        project_forms.initialize(self, group, group=True)
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
        selected = data.get('motivation_role') if 'motivation_role' in self.data else project_forms.INHERIT
        role = data.get('portal_role') if selected == project_forms.INHERIT else selected
        if role in REPORT_ROLES and not data.get('subdivisions'):
            self.add_error('subdivisions', 'Выберите хотя бы одно подразделение для отчётов.')
        actor = self.actor
        if self.group and actor and not actor.is_superuser and self.group.user_set.filter(pk=actor.pk).exists():
            is_admin_group = PortalReportGroup.objects.filter(group=self.group, portal_role=ADMIN).exists()
            other_admin = actor.groups.filter(name=ADMIN).exists() or PortalReportGroup.objects.filter(group__user=actor, portal_role=ADMIN).exclude(group=self.group).exists()
            keeps_admin = data.get('portal_role') == ADMIN and actor.pk in {user.pk for user in data.get('members', [])}
            if is_admin_group and not other_admin and not keeps_admin:
                self.add_error(None, 'Нельзя снять собственный доступ общего администратора через изменение группы. Используйте другую учётную запись администратора.')
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
        selected = self.cleaned_data.get('motivation_role') if 'motivation_role' in self.data else project_forms.INHERIT
        motivation_role = role if selected == project_forms.INHERIT else selected
        worker_name = 'Продажи' if motivation_role == SALES else 'Политики' if motivation_role in (POLITICIAN, DEPUTY_DIRECTOR) else None
        if 'motivation_role' in self.data and selected != project_forms.INHERIT:
            worker = None
        if worker is None and worker_name:
            worker = TypeCoeff.objects.filter(type_coeff_name=worker_name).order_by('pk').first()
        scope, _ = PortalReportGroup.objects.update_or_create(group=group, defaults={'employee_type': worker, 'portal_role': role})
        project_forms.save_assignments(self, group, group=True)
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
    project_context = selector(request, 'portal_user_groups')
    project = project_context['selected_project']
    group = None
    identifier = request.POST.get('group') if request.method == 'POST' else request.GET.get('edit')
    if identifier:
        if not identifier.isdigit():
            raise Http404
        group = get_object_or_404(Group.objects.exclude(name__in=ROLES), pk=int(identifier))
    form = ReportGroupForm(request.POST if request.method == 'POST' else None, group=group, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        messages.success(request, f'Группа «{saved.name}» сохранена. Доступ участников обновлён. Персональные назначения сохраняют приоритет. Участники увидят изменения при следующем открытии страницы.')
        from django.urls import reverse
        return redirect(reverse('portal_user_groups') + project_context['project_query'])
    catalog = list(Group.objects.exclude(name__in=ROLES).select_related('portal_scope__employee_type').prefetch_related('project_access', 'user_set', 'portal_scope__subdivisions__merged_into').order_by('name'))
    catalog = [row for row in catalog if not project or project in group_roles(row)]
    for row in catalog:
        scope = getattr(row, 'portal_scope', None)
        from .access import PROJECT_ROLES, PROJECT_LABELS, legacy_project_role
        overrides = {grant.project: grant.role for grant in row.project_access.all()}
        legacy = scope.portal_role if scope else ''
        row.project_access_configured = scope is not None or bool(overrides)
        row.project_access_label = ' · '.join(PROJECT_LABELS[project] + ': ' + role for project, allowed in PROJECT_ROLES.items() if (role := overrides.get(project, legacy_project_role(legacy,project) or ''))) or 'Нет доступа'
        if project:
            row.project_access_label = PROJECT_LABELS[project] + ': ' + group_roles(row)[project]
        row.reporting_branches = sorted({(branch.merged_into or branch).subdivision_key for branch in scope.subdivisions.all()}) if scope else []
    configured = sum(row.project_access_configured for row in catalog)
    return render(request, 'base/user_groups.html', {**project_context, 'form': form, 'editing': group, 'catalog': catalog, 'configured': configured})
