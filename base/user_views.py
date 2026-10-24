from . import project_access_forms as project_forms
from django import forms
from django.contrib import messages
from django.contrib.auth.models import User, Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, Count
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from .access import ADMIN, ROLES, role_for, can_access, project_role_summary
from .admin import PortalRoleFormMixin
from .models import MotivationReportAccess
from .directory_search import search_accounts, DirectoryPending


class UserForm(PortalRoleFormMixin, forms.ModelForm):
    username = PortalRoleFormMixin.username
    motivation_role = project_forms.project_field('motivation')
    purchases_role = project_forms.project_field('purchases')
    dashboards_role = project_forms.project_field('dashboards')
    portal_role = PortalRoleFormMixin.portal_role
    report_subdivision = PortalRoleFormMixin.report_subdivision
    report_author = PortalRoleFormMixin.report_author
    password1 = forms.CharField(label='Пароль', required=False, widget=forms.PasswordInput(render_value=False))
    password2 = forms.CharField(label='Повтор пароля', required=False, widget=forms.PasswordInput(render_value=False))
    groups = forms.ModelMultipleChoiceField(label='Группы доступа', queryset=Group.objects.exclude(name__in=ROLES).order_by('name'), required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active', 'groups']
        labels = {'first_name': 'Имя и отчество', 'last_name': 'Фамилия', 'email': 'Электронная почта', 'is_active': 'Пользователь активен'}

    def __init__(self, *args, actor, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['username'].disabled = True

    def clean(self):
        data = super().clean()
        password = data.get('password1', '')
        if '\\' in data.get('username', ''):
            if password or data.get('password2'):
                self.add_error('password1', 'Пароль доменного пользователя проверяет Windows. Оставьте поля пустыми.')
        else:
            if not self.instance.pk and not password:
                self.add_error('password1', 'Задайте пароль локального пользователя.')
            if password != data.get('password2', ''):
                self.add_error('password2', 'Пароли не совпадают.')
            if password:
                validate_password(password, self.instance)
        if self.instance.is_superuser and data.get('portal_role') != ADMIN:
            self.add_error('portal_role', 'Суперпользователю необходимо оставить роль администратора.')
        if self.instance.pk == self.actor.pk and (not data.get('is_active') or getattr(self, '_effective_role', data.get('portal_role')) != ADMIN):
            self.add_error(None, 'Нельзя отключить собственную учётную запись или снять свою роль администратора.')
        return data

    @transaction.atomic
    def save(self):
        user = super().save(commit=False)
        if '\\' in user.username:
            user.set_unusable_password()
        elif self.cleaned_data.get('password1'):
            user.set_password(self.cleaned_data['password1'])
        user.is_staff = self._effective_role == ADMIN
        user.save()
        user.groups.set(self.cleaned_data['groups'])
        if self.cleaned_data.get('portal_role'):
            user.groups.add(Group.objects.get_or_create(name=self.cleaned_data['portal_role'])[0])
        project_forms.save_assignments(self, user)
        branch, author = self.cleaned_data.get('report_subdivision'), self.cleaned_data.get('report_author', '').strip()
        if branch and author:
            MotivationReportAccess.objects.update_or_create(user=user, defaults={'subdivision': branch, 'deal_author': author})
        else:
            MotivationReportAccess.objects.filter(user=user).delete()
        return user


class UserFilters(forms.Form):
    role = forms.MultipleChoiceField(label='Роли', required=False, choices=[(r, r) for r in ROLES] + [('none', 'Без доступа')], widget=forms.CheckboxSelectMultiple)
    group = forms.MultipleChoiceField(label='Группы', required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['group'].choices = [(str(pk), name) for pk, name in Group.objects.exclude(name__in=ROLES).order_by('name').values_list('pk', 'name')] + [('none', 'Без группы')]


@require_http_methods(['GET', 'POST'])
def users(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    identifier = request.POST.get('user') if request.method == 'POST' else request.GET.get('edit')
    if identifier and not identifier.isdigit():
        raise Http404
    editing = get_object_or_404(User, pk=identifier) if identifier and identifier.isdigit() else None
    form = UserForm(request.POST if request.method == 'POST' else None, actor=request.user, instance=editing, initial={'is_active': True} if not editing else None)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        messages.success(request, f'Пользователь «{saved.username}» сохранён.')
        return redirect('portal_users')
    query = request.GET.get('q', '').strip()[:150]
    catalog = User.objects.prefetch_related('groups__project_access', 'project_access').order_by('username')
    if query:
        catalog = catalog.filter(Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(email__icontains=query))
    filters = UserFilters(request.GET)
    if filters.is_valid():
        roles, groups = filters.cleaned_data['role'], filters.cleaned_data['group']
        if groups:
            condition = Q(groups__pk__in=[int(value) for value in groups if value != 'none'])
            if 'none' in groups:
                catalog = catalog.annotate(access_groups=Count('groups', filter=~Q(groups__name__in=ROLES), distinct=True))
                condition |= Q(access_groups=0)
            catalog = catalog.filter(condition)
    else:
        catalog = catalog.none()
    role_filters = set(filters.cleaned_data.get('role', [])) if filters.is_valid() else set()
    rows = []
    from .access import PROJECT_ROLES
    for row in catalog.distinct():
        effective = {role_for(row, project) for project in PROJECT_ROLES} - {None, ''}
        if role_filters and not (effective & role_filters or ('none' in role_filters and not effective)):
            continue
        rows.append(row)
        if len(rows) == 200:
            break
    catalog = rows
    for row in catalog:
        row.portal_role_label = project_role_summary(row)
    sections = {}
    selected = set(filters.cleaned_data.get('group', [])) if filters.is_valid() else set()
    for row in catalog:
        memberships = [group for group in row.groups.all() if group.name not in ROLES]
        if selected:
            memberships = [group for group in memberships if str(group.pk) in selected]
        destinations = [(f'group-{group.pk}', group.name) for group in memberships]
        if not destinations:
            destinations = [('role-' + (role_for(row) or 'none'), role_for(row) or 'Без группы')]
        for key, name in destinations:
            section = sections.setdefault(key, {'key': key, 'name': name, 'users': []})
            section['users'].append(row)
    sections = sorted(sections.values(), key=lambda section: (section['name'] == 'Без группы', section['name'].casefold()))
    return render(request, 'base/users.html', {'form': form, 'editing': editing, 'catalog': catalog, 'sections': sections, 'query': query, 'filters': filters, 'filter_query': request.GET.urlencode()})


@require_GET
def directory(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    query = request.GET.get('q', '').strip()[:100]
    if len(query) < 3:
        return JsonResponse({'error': 'Введите не менее трёх символов имени или логина.'}, status=400)
    try:
        options = {'background': True}
        if request.GET.get('domain'):
            options['domain'] = request.GET['domain'][:150]
        return JsonResponse({'users': search_accounts(query, **options)})
    except DirectoryPending as error:
        return JsonResponse({'pending': True, 'message': str(error)}, status=202)
    except RuntimeError as error:
        return JsonResponse({'error': str(error)}, status=503)
    except Exception:
        return JsonResponse({'error': 'Поиск AD недоступен. Проверьте доступ Windows-сервера к домену.'}, status=503)


@require_GET
def directory_domains(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    from .windows_domains import domain_map
    try:
        domains = domain_map(background=True)
        return JsonResponse({'domains': [{'name': name.upper(), 'dns': dns} for name, dns in sorted(domains.items()) if name]})
    except DirectoryPending as error:
        return JsonResponse({'pending': True, 'message': str(error)}, status=202)
    except Exception:
        return JsonResponse({'error': 'Список доменов AD недоступен. Основной домен можно использовать; для остальных проверьте доступ Windows к доменам и доверительным отношениям.'}, status=503)
