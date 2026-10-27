"""Read-only explanations of the same access rules used by report views."""
from .access import role_for, legacy_role_for, PROJECT_LABELS, REPORT_ROLES, ADMIN
from .models import UserProjectAccess, GroupProjectAccess


def effective_access(user):
    personal={grant.project:grant.role for grant in UserProjectAccess.objects.filter(user=user)}
    groups=list(user.groups.all())
    grants=list(GroupProjectAccess.objects.filter(group__in=groups).select_related('group'))
    result=[]
    for project,label in PROJECT_LABELS.items():
        role=role_for(user,project)
        inherited=[grant for grant in grants if grant.project==project]
        conflict=len({grant.role for grant in inherited if grant.role})>1 and project not in personal
        if not user.is_active:source='Учётная запись отключена'
        elif legacy_role_for(user)==ADMIN:source='Общий администратор портала'
        elif project in personal:source='Персональное назначение' if personal[project] else 'Персональный запрет'
        elif inherited:source='Группы: '+', '.join(sorted({grant.group.name for grant in inherited}))
        else:source='Прежняя роль / наследование' if role else 'Нет назначения'
        description='Доступ к проекту отсутствует.' if not role else 'Работа в проекте в рамках назначенной роли.'
        branches=[];author='';warning=''
        if conflict and not role:warning='В группах назначены разные роли. Устраните конфликт или задайте персональную роль.'
        if project=='purchases' and role=='Аналитик':
            description='Просмотр модели закупок и скачивание Excel. Изменение данных, импорт, экспорт в MS SQL и запуск расчёта недоступны.'
        if project=='motivation' and role:
            if role in REPORT_ROLES:
                from .report_access import politician_scope
                scope=politician_scope(user)
                if scope:
                    branches=scope['subdivision'] if isinstance(scope['subdivision'],list) else [scope['subdivision']]
                    author=scope.get('author','')
                    description='Свои сделки и премия.' if not scope.get('all_authors') else 'Все сотрудники выбранных подразделений.' if role=='Директор' else 'Все политики подразделений; директорская премия — только своя.'
                else:warning='Личные отчёты недоступны: проверьте подразделения и привязку автора сделки.'
            else:
                description={'Администратор':'Все подразделения, управление настройками и расчётами.','Моделятор':'Настройка модели мотивации и коэффициентов.','Генеральный директор':'Просмотр отчётов всех подразделений и согласование модели.','Финансист':'Отчёты, утверждённые настройки и финансовое согласование.','Смотрящий':'Просмотр мотивации без изменения данных.'}.get(role,description)
                branches=['Все подразделения']
        result.append({'project':project,'label':label,'role':role or 'Нет доступа','source':source,'description':description,'branches':branches,'author':author,'warning':warning,'personal':project in personal})
    return result
