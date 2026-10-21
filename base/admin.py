from django import forms
from django.contrib import admin
from django.urls import reverse_lazy
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.forms import UserChangeForm, AdminUserCreationForm, UsernameField
from django.contrib.auth.models import User, Group
from django.contrib.auth.validators import UnicodeUsernameValidator
import re
from .access import ADMIN, ROLES, POLITICIAN, role_for
from .models import MotivationReportAccess, PortalReportGroup
from admin_motivation.models import Subdivision


class PortalRoleFormMixin:
    username = UsernameField(label='Логин', max_length=150, help_text='Локальный: обычный логин. Доменный: DOMAIN\\login, например DAICHI\\etladmin. Для доменного пользователя пароль не задавайте — его проверяет Windows.')

    def clean_username(self):
        username = self.cleaned_data['username']
        if '\\' in username:
            parts = username.split('\\')
            if len(parts) != 2 or not re.fullmatch(r'[A-Za-z0-9_.-]+', parts[0]) or not parts[1] or '@' in parts[1]:
                raise forms.ValidationError('Укажите доменный логин в формате DOMAIN\\login (не UPN).')
            UnicodeUsernameValidator()(parts[1])
            username = username.casefold()
            self._domain_username = True
        else:
            UnicodeUsernameValidator()(username)
        duplicate = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError('Пользователь с таким логином уже существует.')
        return username

    def _get_validation_exclusions(self):
        exclusions = super()._get_validation_exclusions()
        if getattr(self, '_domain_username', False):
            # Django's default model validator forbids the Windows separator.
            # The form above validates the full name and checks uniqueness.
            exclusions.add('username')
        return exclusions

    report_subdivision = forms.ModelChoiceField(queryset=Subdivision.objects.all(), label='Подразделение для личных отчётов', required=False, widget=forms.Select(attrs={'data-author-url': reverse_lazy('portal_report_authors')}))
    report_author = forms.CharField(label='Точное ФИО автора сделки', max_length=255, required=False, widget=forms.TextInput(attrs={'list': 'report-author-options', 'autocomplete': 'off'}), help_text='Для роли «Политик» ФИО заполняется из карточки пользователя. При необходимости исправьте по точному ФИО из отчётов.')

    portal_role = forms.ChoiceField(label='Роль в портале', choices=[('', 'По группе · без группы нет доступа')] + [(r, r) for r in ROLES], required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['portal_role'].initial = ADMIN if self.instance.is_superuser else self.instance.groups.filter(name__in=ROLES).values_list('name', flat=True).first()
        if self.instance.pk:
            access = MotivationReportAccess.objects.select_related('subdivision').filter(user=self.instance).first()
            if access:
                self.fields['report_subdivision'].initial = access.subdivision.merged_into_id or access.subdivision_id
                self.fields['report_author'].initial = access.deal_author
        self.fields['portal_role'].widget.attrs['data-report-groups'] = ','.join(str(value) for value in PortalReportGroup.objects.values_list('group_id', flat=True))
        if 'groups' in self.fields:

            self.fields['groups'].queryset = Group.objects.exclude(name__in=ROLES)

    def clean(self):
        data = super().clean()
        if data.get('is_superuser') and data.get('portal_role') != ADMIN:
            self.add_error('portal_role', 'Суперпользователь должен иметь роль «Администратор». Для другой роли снимите признак суперпользователя.')
        selected_groups = data.get('groups', []) if 'groups' in self.fields else self.instance.groups.all() if self.instance.pk else []
        report_groups = list(PortalReportGroup.objects.filter(group__in=selected_groups).prefetch_related('subdivisions__merged_into'))
        if report_groups and data.get('portal_role') == POLITICIAN:
            # Access follows membership; do not retain a standalone role after removal.
            data['portal_role'] = ''
        effective_role = data.get('portal_role') or (POLITICIAN if report_groups else '')
        if effective_role == POLITICIAN:
            if report_groups:
                branch_ids = {branch.merged_into_id or branch.pk for group in report_groups for branch in group.subdivisions.all()}
                data['report_subdivision'] = self.fields['report_subdivision'].queryset.filter(pk__in=branch_ids).order_by('subdivision_key').first()
            if not data.get('report_author', '').strip() and 'report_author' not in self.errors:
                first = data.get('first_name', '') if 'first_name' in self.fields else self.instance.first_name
                last = data.get('last_name', '') if 'last_name' in self.fields else self.instance.last_name
                if first and last:
                    fullname = ' '.join((last + ' ' + first).split())
                    try:
                        data['report_author'] = self.fields['report_author'].clean(fullname)
                    except forms.ValidationError as error:
                        self.add_error('report_author', error)
            if not data.get('report_subdivision'):
                self.add_error('report_subdivision', 'Назначьте подразделение для роли «Политик».')
            if not data.get('report_author', '').strip() and 'report_author' not in self.errors:
                self.add_error('report_author', 'Назначьте точное ФИО автора сделки для роли «Политик».')
        return data


class PortalUserChangeForm(PortalRoleFormMixin, UserChangeForm):
    username = PortalRoleFormMixin.username
    portal_role = PortalRoleFormMixin.portal_role
    report_subdivision = PortalRoleFormMixin.report_subdivision
    report_author = PortalRoleFormMixin.report_author


class PortalUserCreationForm(PortalRoleFormMixin, AdminUserCreationForm):
    username = PortalRoleFormMixin.username
    portal_role = PortalRoleFormMixin.portal_role
    report_subdivision = PortalRoleFormMixin.report_subdivision
    report_author = PortalRoleFormMixin.report_author

    def validate_passwords(self, *args, **kwargs):
        if getattr(self, '_domain_username', False):
            self.cleaned_data['usable_password'] = 'false'
        return super().validate_passwords(*args, **kwargs)


admin.site.unregister(User)


@admin.register(User)
class PortalUserAdmin(UserAdmin):
    form = PortalUserChangeForm
    add_form = PortalUserCreationForm
    list_display = ('username', 'first_name', 'last_name', 'email', 'portal_role_label', 'is_active')
    list_filter = ('is_active', 'is_staff', 'groups')

    @admin.display(description='Роль в портале')
    def portal_role_label(self, obj):
        return role_for(obj) or 'Без доступа'

    fieldsets = UserAdmin.fieldsets + (('Доступ к порталу', {'fields': ('portal_role', 'report_subdivision', 'report_author')}),)
    add_fieldsets = UserAdmin.add_fieldsets + (('Доступ к порталу', {'fields': ('portal_role', 'report_subdivision', 'report_author')}),)

    class Media:
        js = ('base/admin_report_access.js',)

    def save_model(self, request, obj, form, change):
        obj.is_staff = form.cleaned_data.get('portal_role') == ADMIN
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        user = form.instance
        user.groups.remove(*Group.objects.filter(name__in=ROLES))
        role = form.cleaned_data.get('portal_role')
        if role:
            user.groups.add(Group.objects.get(name=role))
        subdivision, author = form.cleaned_data.get('report_subdivision'), form.cleaned_data.get('report_author', '').strip()
        if subdivision and author:
            MotivationReportAccess.objects.update_or_create(user=user, defaults={'subdivision': subdivision, 'deal_author': author})
        else:
            MotivationReportAccess.objects.filter(user=user).delete()


class PortalGroupForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(label='Участники группы', queryset=User.objects.order_by('username'), required=False,
        widget=admin.widgets.FilteredSelectMultiple('Участники', is_stacked=False),
        help_text='Дополнительная группа пользователей. Персональная роль и привязка к личным отчётам назначаются в карточке пользователя.')

    class Meta:
        model = Group
        fields = ['name', 'permissions', 'members']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['members'].initial = self.instance.user_set.all()


admin.site.unregister(Group)


@admin.register(Group)
class PortalGroupAdmin(GroupAdmin):
    form = PortalGroupForm
    filter_horizontal = ('permissions',)

    def get_fields(self, request, obj=None):
        return ('name', 'permissions') if obj and obj.name in ROLES else ('name', 'permissions', 'members')

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.instance.name not in ROLES:
            form.instance.user_set.set(form.cleaned_data.get('members', []))

    def get_readonly_fields(self, request, obj=None):
        return ('name', 'permissions') if obj and obj.name in ROLES else ()

    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.name in ROLES) and super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset.exclude(name__in=ROLES))
