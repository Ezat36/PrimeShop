from django.db import migrations


def release_payments_from_canceled_sales(apps, schema_editor):
    CustomerPayment = apps.get_model('shop', 'CustomerPayment')
    CustomerPayment.objects.filter(sale__is_canceled=True).update(sale=None)


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0018_sale_cancel_reason_sale_canceled_at_sale_canceled_by_and_more'),
    ]

    operations = [
        migrations.RunPython(
            release_payments_from_canceled_sales,
            migrations.RunPython.noop,
        ),
    ]
