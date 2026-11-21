"""Archive is list organization; plan status and snapshot remain unchanged."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import SplitScenario,SplitScenarioEvent
from .sales_planning_access import check_scenario


@transaction.atomic
def set_archived(scenario,actor,revision,archived):
    check_scenario(actor,scenario)
    if str(revision)!=str(scenario.revision):raise ValidationError('Сценарий изменён. Обновите страницу и повторите действие.')
    if bool(scenario.archived_at)==archived:return
    updated=SplitScenario.objects.filter(pk=scenario.pk,revision=scenario.revision).update(archived_at=timezone.now() if archived else None,revision=scenario.revision+1)
    if not updated:raise ValidationError('Сценарий изменён. Обновите страницу и повторите действие.')
    SplitScenarioEvent.objects.create(scenario=scenario,actor=actor,revision=scenario.revision+1,action='Сценарий помещён в архив' if archived else 'Сценарий восстановлен из архива')
    scenario.refresh_from_db(fields=['archived_at','revision'])
