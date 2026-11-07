from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('base', '0007_salesplanninggoods')]
    operations = [
        migrations.RenameField(model_name='salesplanninggoods', old_name='parent_key', new_name='product_group'),
        migrations.AlterField(model_name='salesplanninggoods', name='product_group', field=models.TextField('Товарная группа', blank=True)),
        migrations.AlterField(model_name='salesplanninggoods', name='brand', field=models.TextField('Бренд', blank=True)),
        migrations.AlterField(model_name='salesplanninggoods', name='power_class', field=models.TextField('Мощность', blank=True)),
        migrations.AlterField(model_name='salesplanninggoods', name='outdoor_block_type', field=models.TextField('INV / On-Off', blank=True)),
    ]
