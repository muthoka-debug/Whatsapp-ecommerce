"""What each event actually does.

Handlers are the only place that decides "an order was paid, therefore send a
receipt". They are deliberately thin: the work itself belongs to the existing
services (NotificationService, and later the AI assistant), so the same action
can be triggered by automation, by a staff member, or by a test.

Every handler runs inside a Celery worker, receives `(run, business, payload)`,
and re-reads its subject from the database — the row may have changed in the
seconds between the event and the worker picking it up, and for a delayed task
it may have changed over two days.

Handlers are registered by importing this module; see AutomationConfig.ready().
"""

import logging
from datetime import timedelta

from django.conf import settings

from notifications.services import NotificationService

from .events import Event
from .services import emit, handles

logger = logging.getLogger(__name__)


def _load(model, run, *, select_related=()):
    """Re-read the event's subject, scoped to the event's business.

    The business filter is not decoration: a task argument is just an ID, and
    scoping every read to run.business means a malformed or replayed task can
    never reach another tenant's row.
    """
    if not run.subject_id:
        return None
    queryset = model.objects.filter(business=run.business)
    if select_related:
        queryset = queryset.select_related(*select_related)
    return queryset.filter(pk=run.subject_id).first()


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@handles(Event.ORDER_CREATED)
def notify_order_created(run, business, payload):
    from orders.models import Order

    order = _load(Order, run, select_related=('customer',))
    if order is None:
        logger.warning('automation: order %s vanished before confirmation', run.subject_id)
        return
    NotificationService(business).send_order_confirmation(order)


@handles(
    Event.ORDER_CONFIRMED,
    Event.ORDER_PAID,
    Event.ORDER_PROCESSING,
    Event.ORDER_READY,
    Event.ORDER_SHIPPED,
    Event.ORDER_DELIVERED,
    Event.ORDER_CANCELLED,
)
def notify_order_status(run, business, payload):
    from orders.models import Order

    order = _load(Order, run, select_related=('customer',))
    if order is None:
        return
    NotificationService(business).send_order_status_update(
        order,
        payload.get('old_status', ''),
        payload.get('new_status', order.status),
    )


@handles(Event.ORDER_DELIVERED)
def schedule_feedback_request(run, business, payload):
    """Ask for feedback a couple of days after delivery.

    Scheduled as a delayed event rather than a sleeping task, so it survives a
    worker restart and shows up in the audit trail as a pending run.
    """
    from orders.models import Order

    order = _load(Order, run)
    if order is None:
        return

    hours = getattr(settings, 'DELIVERY_FEEDBACK_AFTER_HOURS', 48)
    emit(
        Event.FEEDBACK_DUE,
        business,
        subject=order,
        idempotency_key=f'feedback:{order.pk}',   # one request per order, ever
        delay=timedelta(hours=hours),
    )


@handles(Event.FEEDBACK_DUE)
def request_feedback(run, business, payload):
    from orders.models import Order

    order = _load(Order, run, select_related=('customer',))
    if order is None:
        return
    # Two days is long enough for a delivery to be reversed.
    if order.status != 'DELIVERED':
        logger.info('automation: order %s no longer delivered; skipping feedback', order.pk)
        return
    NotificationService(business).send_feedback_request(order)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

@handles(Event.PAYMENT_COMPLETED)
def notify_payment_completed(run, business, payload):
    from payments.models import Payment

    payment = _load(Payment, run, select_related=('order',))
    if payment is None:
        return
    NotificationService(business).send_payment_confirmation(payment)


@handles(Event.PAYMENT_FAILED)
def notify_payment_failed(run, business, payload):
    from payments.models import Payment

    payment = _load(Payment, run, select_related=('order',))
    if payment is None:
        return
    NotificationService(business).send_payment_failed(payment)


# ---------------------------------------------------------------------------
# Deliveries
# ---------------------------------------------------------------------------

@handles(
    Event.DELIVERY_ASSIGNED,
    Event.DELIVERY_OUT_FOR_DELIVERY,
    Event.DELIVERY_DELIVERED,
    Event.DELIVERY_FAILED,
)
def notify_delivery_update(run, business, payload):
    from deliveries.models import Delivery

    delivery = _load(Delivery, run, select_related=('order',))
    if delivery is None:
        return
    NotificationService(business).send_delivery_update(
        delivery, payload.get('delivery_event')
    )


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@handles(Event.PRODUCT_STOCK_LOW, Event.PRODUCT_OUT_OF_STOCK)
def alert_low_stock(run, business, payload):
    from products.models import Product

    product = _load(Product, run)
    if product is None:
        return
    # Stock may have been replenished between the event and this run.
    if not product.is_low_stock and product.stock > 0:
        return
    NotificationService(business).send_low_stock_alert(product)


# ---------------------------------------------------------------------------
# Customer lifecycle
# ---------------------------------------------------------------------------

@handles(Event.CART_ABANDONED)
def remind_abandoned_cart(run, business, payload):
    from Storefront.models import Cart

    cart = _load(Cart, run, select_related=('customer',))
    if cart is None:
        return
    # Checked out or emptied since the sweep queued this.
    if not cart.is_active or not cart.items.exists():
        return
    NotificationService(business).send_abandoned_cart_reminder(cart)


@handles(Event.CUSTOMER_INACTIVE)
def flag_inactive_customer(run, business, payload):
    """Record the customer as a win-back candidate.

    Deliberately does not message them: unsolicited outbound to a customer who
    has gone quiet is the fastest way to get a WhatsApp sender number blocked.
    The dashboard surfaces these and staff decide.
    """
    from analytics.models import AIInsight
    from customers.models import Customer

    customer = _load(Customer, run)
    if customer is None:
        return

    days = payload.get('inactive_days', '')
    AIInsight.objects.create(
        business=business,
        insight_type='CUSTOMER_SEGMENT',
        title=f'{customer.name} has gone quiet',
        description=(
            f'{customer.name} has not ordered in {days} days. '
            f'Consider a win-back offer.'
        ),
        priority='LOW',
        data={'customer_id': str(customer.id), 'inactive_days': days},
        is_actionable=True,
        action_url=f'/dashboard/customers/?q={customer.phone}',
    )
