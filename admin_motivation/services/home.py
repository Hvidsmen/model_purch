"""Role-specific starting points; no DWH reads or payroll amounts on the landing page."""
from base.access import role_for, ADMIN, MODELER, FINANCIER, DIRECTOR, PERSONAL_ROLES
from django.urls import reverse
from .reports_next import available


def home_context(user):
    role = role_for(user, 'motivation')
    catalog = available(user)
    primary = 'mine' if role in PERSONAL_ROLES else 'registry' if role == FINANCIER else 'executive' if role == DIRECTOR else 'team'
    cards = [{'title': title, 'description': description, 'url': reverse('motivation_report_next', args=[key]) + ('?run=1' if key == primary and role not in (ADMIN, MODELER) else ''), 'primary': key == primary} for key, (title, description) in catalog.items()]
    if role in (ADMIN, MODELER):
        for card in cards:
            card['primary'] = False
        cards.insert(0, {'title': 'Работа с моделью', 'description': 'Открыть черновик, изменить коэффициенты и проверить влияние на план премий.', 'url': reverse('global_coeff_admin_motivation'), 'primary': True})
        cards.insert(1, {'title': 'Проверить коэффициенты', 'description': 'Найти пропуски и создать недостающие ставки перед согласованием.', 'url': reverse('motivation_coefficient_coverage'), 'primary': False})
    if role in (ADMIN, MODELER, FINANCIER, DIRECTOR):
        cards.append({'title': 'На проверке', 'description': 'Посмотреть замечания и решения по версии модели.', 'url': reverse('motivation_reviews'), 'primary': False})
    if role == ADMIN:
        cards.append({'title': 'Расчёты и задания', 'description': 'Запустить расчёт, проверить состояние и открыть результат.', 'url': reverse('motivation_calculations'), 'primary': False})
    descriptions = {
        ADMIN: 'Настройте модель, проверьте данные и запустите расчёт.',
        MODELER: 'Изменяйте модель в черновике, проверяйте результат и отправляйте на согласование.',
        FINANCIER: 'Проверьте рассчитанные суммы, замечания и реестр премий.',
        DIRECTOR: 'Сравните план премий и рассмотрите модель, отправленную на согласование.',
    }
    return {'home_cards': sorted(cards, key=lambda card: not card['primary']), 'home_description': descriptions.get(role, 'Посмотрите премии и откройте объяснение каждой суммы.'), 'home_personal': role in PERSONAL_ROLES}
