"""Backend-specific DDL for indexed text; SQLite and model state stay intact."""
from django.db import migrations, models

POWER_KEY_LENGTH = 255


class CreateBlockPairModel(migrations.CreateModel):
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == 'microsoft':
            # nvarchar(max) cannot participate in the (group_id, power) key.
            # Adapt a private state copy, leaving the source SQLite schema and
            # historical/runtime TextField semantics unchanged.
            to_state = to_state.clone()
            to_state.alter_field(
                app_label, self.name_lower, 'power',
                models.CharField('Мощность', max_length=POWER_KEY_LENGTH),
                preserve_default=True,
            )
        return super().database_forwards(app_label, schema_editor, from_state, to_state)
