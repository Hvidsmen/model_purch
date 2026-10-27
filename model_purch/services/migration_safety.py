"""Conservative exemption for label-only forward migrations, never SQL/data changes."""
from django.db.migrations.operations.fields import AlterField
from django.db.migrations import Migration


def metadata_only_plan(executor, plan):
    if not plan or any(backwards for _, backwards in plan):
        return False
    try:
        state = executor._create_project_state(with_applied_migrations=True)
        for migration, _ in plan:
            if type(migration).apply is not Migration.apply:
                return False
            if not migration.operations:
                return False
            for operation in migration.operations:
                # Exact type: custom subclasses can execute arbitrary database code.
                if type(operation) is not AlterField or not operation.preserve_default:
                    return False
                before = state.models[migration.app_label, operation.model_name_lower].get_field(operation.name).deconstruct()
                operation.state_forwards(migration.app_label, state)
                after = state.models[migration.app_label, operation.model_name_lower].get_field(operation.name).deconstruct()
                def database_signature(value):
                    name, path, args, kwargs = value
                    if not path.startswith('django.db.models.'):
                        raise ValueError('Custom fields require a backup')
                    kwargs = dict(kwargs)
                    for label in ('verbose_name', 'help_text'):
                        kwargs.pop(label, None)
                    return name, path, args, kwargs
                if database_signature(before) != database_signature(after):
                    return False
        return True
    except Exception:
        # Unknown migration/state means retain the full backup.
        return False
