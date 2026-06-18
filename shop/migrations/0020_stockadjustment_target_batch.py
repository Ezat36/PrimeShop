import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0019_release_payments_from_canceled_sales'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockadjustment',
            name='target_batch',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='stock_adjustments',
                to='shop.purchaseitem',
            ),
        ),
    ]
