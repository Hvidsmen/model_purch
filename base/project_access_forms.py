"""Independent project grants shared by user and group editors."""
from django import forms
from .access import ADMIN, PROJECT_ROLES, PROJECT_LABELS, legacy_project_role
from .models import UserProjectAccess, GroupProjectAccess

INHERIT = 'inherit'


def project_field(project):
    return forms.ChoiceField(label=PROJECT_LABELS[project], required=False,
        choices=[(INHERIT, 'Наследовать · группы / прежние права'), ('', 'Нет доступа')] + [(role, role) for role in PROJECT_ROLES[project]])


def initialize(form, owner, group=False):
    model = GroupProjectAccess if group else UserProjectAccess
    key = 'group' if group else 'user'
    saved = dict(model.objects.filter(**{key: owner}).values_list('project', 'role')) if owner and owner.pk else {}
    for project in PROJECT_ROLES:
        field = project + '_role'
        form.fields[field].initial = saved.get(project, INHERIT)
        if owner and owner.pk:
            from .access import role_for
            legacy = getattr(getattr(owner, 'portal_scope', None), 'portal_role', '') if group else None
            current = (saved.get(project, legacy_project_role(legacy,project) or '') if group else role_for(owner, project)) or 'Нет доступа'
            form.fields[field].choices = [(INHERIT, 'Наследовать · сейчас: ' + current), *list(form.fields[field].choices)[1:]]
        form.fields[field].widget.attrs['data-project-role'] = project
        form.fields[field].help_text = 'Персональное назначение имеет приоритет над группами.' if not group else 'Роль действует только в этом проекте.'


def motivation_role(form, data, legacy, groups=()):
    if legacy == ADMIN:
        return ADMIN
    field = 'motivation_role'
    if field in form.data:
        selected = data.get(field, '')
    elif getattr(form, 'instance', None) and form.instance.pk:
        saved = UserProjectAccess.objects.filter(user=form.instance, project='motivation').first()
        selected = saved.role if saved else INHERIT
    else:
        selected = INHERIT
    if selected != INHERIT:
        return selected
    assignments = list(GroupProjectAccess.objects.filter(group__in=groups, project='motivation').values_list('role', flat=True))
    if assignments:
        roles = {role for role in assignments if role}
        if len(roles) > 1:
            form.add_error(field, 'У групп разные роли в мотивации. Выберите одну роль пользователя явно.')
        return next(iter(roles)) if len(roles) == 1 else ''
    return legacy


def save_assignments(form, owner, group=False):
    model = GroupProjectAccess if group else UserProjectAccess
    key = 'group' if group else 'user'
    for project in PROJECT_ROLES:
        field = project + '_role'
        if field not in form.data:
            continue  # Older clients keep existing assignments unchanged.
        filters = {key: owner, 'project': project}
        value = form.cleaned_data[field]
        if value == INHERIT:
            model.objects.filter(**filters).delete()
        else:
            model.objects.update_or_create(**filters, defaults={'role': value})
    getattr(owner, '_prefetched_objects_cache', {}).pop('project_access', None)
    if hasattr(owner, '_portal_role'):
        delattr(owner, '_portal_role')
