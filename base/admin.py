from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.forms import UserChangeForm, AdminUserCreationForm
from django.contrib.auth.models import User, Group
from .access import ADMIN, ROLES, role_for


class PortalRoleFormMixin:
    portal_role = forms.ChoiceField(label='Роль в портале', choices=[('', 'Без доступа')] + [(r, r) for r in ROLES], required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['portal_role'].initial = role_for(self.instance)
        if 'groups' in self.fields:
            self.fields['groups'].queryset = Group.objects.exclude(name__in=ROLES)

    def clean(self):
        data = super().clean()
        if data.get('is_superuser') and data.get('portal_role') != ADMIN:
            self.add_error('portal_role', 'Суперпользователь должен иметь роль «Администратор». Для другой роли снимите признак суперпользователя.')
        return data


class PortalUserChangeForm(PortalRoleFormMixin, UserChangeForm):
    portal_role = PortalRoleFormMixin.portal_role


class PortalUserCreationForm(PortalRoleFormMixin, AdminUserCreationForm):
    portal_role = PortalRoleFormMixin.portal_role


admin.site.unregister(User)


@admin.register(User)
class PortalUserAdmin(UserAdmin):
    form = PortalUserChangeForm
    add_form = PortalUserCreationForm
    fieldsets = UserAdmin.fieldsets + (('Доступ к порталу', {'fields': ('portal_role',)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (('Доступ к порталу', {'fields': ('portal_role',)}),)

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


admin.site.unregister(Group)


@admin.register(Group)
class PortalGroupAdmin(GroupAdmin):
    def get_readonly_fields(self, request, obj=None):
        return ('name', 'permissions') if obj and obj.name in ROLES else ()

    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.name in ROLES) and super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset.exclude(name__in=ROLES))
