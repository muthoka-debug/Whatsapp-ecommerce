"""Celery tasks.

Two kinds live here:

* `run_automation` — executes one emitted event. Every `emit()` ends up here.
* `sweep_*` — periodic scans (Celery Beat) that turn a *condition* into an
  event: a cart that has sat untouched for a day, stock that fell below its
  threshold, a customer who stopped ordering. Conditions have no natural
  trigger point in the request cycle, so they are polled rather than emitted.

Sweeps iterate businesses explicitly and scope every query to one, so a bug
here cannot leak one tenant's carts into another's reminders.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import F, Max
from django.utils import timezone

from .events import Event
from .services import dispatch, emit

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def run_automation(self, run_id):
    """Execute the handlers for one emitted event.

    dispatch() catches handler errors itself and records them, so a retry here
    only happens on an infrastructure failure (database unreachable mid-run).
    """
    dispatch(run_id)


# ---------------------------------------------------------------------------
# Scheduled sweeps
# ---------------------------------------------------------------------------

def _active_businesses():
    from businesses.models import Business

    return Business.objects.filter(is_active=True) if hasattr(
        Business, 'is_active'
    ) else Business.objects.all()


@shared_task
def sweep_abandoned_carts():
    """Emit CART_ABANDONED for carts idle longer than the configured window.

    The idempotency key is per cart, so a cart that stays abandoned is only
    ever reminded once no matter how many times this runs.
    """
    from Storefront.models import Cart

    hours = getattr(settings, 'ABANDONED_CART_AFTER_HOURS', 24)
    cutoff = timezone.now() - timedelta(hours=hours)
    emitted = 0

    for business in _active_businesses():
        carts = (
            Cart.objects.filter(
                business=business,
                is_active=True,
                updated_at__lt=cutoff,
                customer__isnull=False,        # anonymous carts have nobody to remind
            )
            .exclude(customer__phone='')
            .select_related('customer')
        )
        for cart in carts.iterator():
            if not cart.items.exists():
                continue
            if emit(
                Event.CART_ABANDONED,
                business,
                subject=cart,
                idempotency_key=f'cart-abandoned:{cart.pk}',
            ):
                emitted += 1

    logger.info('automation: abandoned-cart sweep emitted %s event(s)', emitted)
    return emitted


@shared_task
def sweep_low_stock():
    """Emit a stock event for every active product at or below its threshold.

    The key includes the date, so the owner gets at most one alert per product
    per day rather than one per sweep — and a product that is still low
    tomorrow is worth mentioning again.
    """
    from products.models import Product

    today = timezone.now().date().isoformat()
    emitted = 0

    for business in _active_businesses():
        products = Product.objects.filter(
            business=business,
            is_active=True,
            stock__lte=F('low_stock_threshold'),
        )
        for product in products.iterator():
            event = (
                Event.PRODUCT_OUT_OF_STOCK if product.stock <= 0
                else Event.PRODUCT_STOCK_LOW
            )
            if emit(
                event,
                business,
                subject=product,
                idempotency_key=f'stock:{product.pk}:{today}',
                stock=product.stock,
                threshold=product.low_stock_threshold,
            ):
                emitted += 1

    logger.info('automation: low-stock sweep emitted %s event(s)', emitted)
    return emitted


@shared_task
def sweep_inactive_customers():
    """Emit CUSTOMER_INACTIVE for customers with no order in the window.

    Customers who have never ordered at all are excluded — they are a lead, not
    a lapsed customer, and lumping the two together makes the segment useless.
    """
    from customers.models import Customer

    days = getattr(settings, 'CUSTOMER_INACTIVE_AFTER_DAYS', 60)
    cutoff = timezone.now() - timedelta(days=days)
    period = timezone.now().date().isoformat()
    emitted = 0

    for business in _active_businesses():
        customers = (
            Customer.objects.filter(business=business)
            .annotate(last_order=Max('orders__placed_at'))
            .filter(last_order__isnull=False, last_order__lt=cutoff)
        )
        for customer in customers.iterator():
            inactive_days = (timezone.now() - customer.last_order).days
            if emit(
                Event.CUSTOMER_INACTIVE,
                business,
                subject=customer,
                idempotency_key=f'inactive:{customer.pk}:{period}',
                inactive_days=inactive_days,
            ):
                emitted += 1

    logger.info('automation: inactive-customer sweep emitted %s event(s)', emitted)
    return emitted
