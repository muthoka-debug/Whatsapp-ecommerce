"""Move the loose Order.delivery_* columns into Delivery rows.

Must run before orders.0003 drops those columns, hence run_before.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    Delivery = apps.get_model('deliveries', 'Delivery')

    deliveries = []
    for order in Order.objects.exclude(
        delivery_address='', delivery_phone=''
    ).select_related('customer').iterator():
        deliveries.append(Delivery(
            business_id=order.business_id,
            order_id=order.id,
            recipient_name=order.customer.name if order.customer_id else '',
            address=order.delivery_address,
            phone=order.delivery_phone,
            notes=order.delivery_notes,
            fee=order.shipping_fee,
            method='RIDER',
            # Reflect where the order already got to, so history is not lost.
            status='DELIVERED' if order.status == 'DELIVERED'
            else 'IN_TRANSIT' if order.status == 'OUT_FOR_DELIVERY'
            else 'CANCELLED' if order.status == 'CANCELLED'
            else 'PENDING',
            delivered_at=order.delivered_at,
            cancelled_at=order.cancelled_at,
        ))

    Delivery.objects.bulk_create(deliveries, batch_size=500)


def backwards(apps, schema_editor):
    """Copy the addresses back onto the orders, then drop the deliveries."""
    Delivery = apps.get_model('deliveries', 'Delivery')
    Order = apps.get_model('orders', 'Order')

    for delivery in Delivery.objects.select_related('order').iterator():
        Order.objects.filter(pk=delivery.order_id).update(
            delivery_address=delivery.address,
            delivery_phone=delivery.phone,
            delivery_notes=delivery.notes,
        )

    Delivery.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('deliveries', '0001_initial'),
        ('orders', '0002_order_is_deleted_alter_order_business_and_more'),
    ]

    run_before = [
        ('orders', '0003_remove_order_delivery_address_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
