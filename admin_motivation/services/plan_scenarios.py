from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from ..models import SalesPlanScenario
from .plan_operations import scenario_operation


def change_scenario(pk, title, source_version, confirm_reset=False):
    with scenario_operation(pk), transaction.atomic():
        scenario = SalesPlanScenario.objects.select_for_update().get(pk=pk, is_deleted=False)
        if source_version != scenario.source_version:
            if scenario.motivationapproval_set.exists():
                raise ValidationError('Сценарий использован при утверждении коэффициентов. Создайте новый сценарий с другим источником.')
            if scenario.loaded_at and not confirm_reset:
                raise ValidationError('Подтвердите сброс загруженного плана и расчёта при смене версии MS SQL.')
            scenario.lines.all().delete()
            scenario.loaded_at = scenario.calculated_at = None
        scenario.title, scenario.source_version = title, source_version
        scenario.full_clean()
        scenario.save()
        return scenario


def delete_scenario(pk):
    with scenario_operation(pk), transaction.atomic():
        scenario = SalesPlanScenario.objects.select_for_update().get(pk=pk, is_deleted=False)
        try:
            scenario.delete()
        except ProtectedError:
            scenario.is_deleted = True
            scenario.save(update_fields=['is_deleted'])
