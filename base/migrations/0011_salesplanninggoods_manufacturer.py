from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('base', '0010_splitscenario_splitscenarioevent_and_more')]
    operations = [migrations.AddField(
        model_name='salesplanninggoods', name='manufacturer',
        field=models.TextField(blank=True, verbose_name='Завод / производитель'))]
