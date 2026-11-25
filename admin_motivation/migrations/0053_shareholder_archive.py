from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('admin_motivation', '0052_distribution_archive')]

    operations = [
        migrations.AddField(
            model_name='shareholdercalculation',
            name='is_archived',
            field=models.BooleanField('В архиве', default=False, db_index=True),
        ),
    ]
