from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from businesses.models import BusinessOwnedModel
from orders.models import Order


class Delivery(BusinessOwnedModel):
    """Fulfilment record for an order.

    One delivery per order. The address/phone/notes used to live as loose
    ``delivery_*`` columns on Order with no status and no fee; they are
    consolidated here so a delivery has its own lifecycle.

    Fee ownership: ``Delivery.fee`` is what a user edits, but the money that
    counts is ``Order.shipping_fee`` — the order is the financial record and
    must not depend on a mutable related row for its total. save() pushes the
    fee onto the order and recalculates its total, so the two never drift.
    """

    DELIVERY_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ASSIGNED', 'Assigned'),
        ('PICKED_UP', 'Picked Up'),
        ('IN_TRANSIT', 'In Transit'),
        ('DELIVERED', 'Delivered'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]

    DELIVERY_METHOD_CHOICES = [
        ('COURIER', 'Courier'),
        ('RIDER', 'In-House Rider'),
        ('PICKUP', 'Customer Pickup'),
        ('POSTAL', 'Postal Service'),
    ]

    # Statuses from which nothing more should happen.
    TERMINAL_STATUSES = ('DELIVERED', 'FAILED', 'CANCELLED')

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name='delivery',
    )

    # Where it goes
    recipient_name = models.CharField(max_length=255, blank=True)
    address = models.TextField()
    phone = models.CharField(max_length=15)
    notes = models.TextField(blank=True)

    # How it gets there
    method = models.CharField(
        max_length=20, choices=DELIVERY_METHOD_CHOICES, default='RIDER'
    )
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=DELIVERY_STATUS_CHOICES, default='PENDING'
    )

    # Who is carrying it. assigned_to is a staff user; courier_name/tracking
    # cover third-party couriers, ahead of the Delivery-API integration in the
    # roadmap's "Later" step.
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_deliveries',
    )
    courier_name = models.CharField(max_length=255, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)

    # Lifecycle
    scheduled_for = models.DateTimeField(null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'deliveries'
        indexes = [
            models.Index(fields=['business', 'status']),
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['tracking_number']),
        ]

    def __str__(self):
        return f"Delivery for Order #{self.order.order_number} - {self.status}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._sync_order_shipping_fee()

    def _sync_order_shipping_fee(self):
        """Keep Order.shipping_fee (and the order total) in step with the fee."""
        order = self.order
        if order.shipping_fee != self.fee:
            order.shipping_fee = self.fee
            order.save(update_fields=['shipping_fee', 'updated_at'])
            order.recalculate_totals()

    # ------------------------------------------------------------------
    # Status transitions
    #
    # Each moves the delivery and, where the roadmap's order statuses have an
    # equivalent, the order too — so OUT_FOR_DELIVERY/DELIVERED on the order are
    # driven by the delivery rather than set by hand in two places.
    # ------------------------------------------------------------------

    def _guard(self):
        if self.status in self.TERMINAL_STATUSES:
            raise ValueError(
                f'Delivery is already {self.get_status_display().lower()}'
            )

    def assign(self, user=None, courier_name='', tracking_number=''):
        """Assign to an in-house rider or a courier."""
        self._guard()
        self.status = 'ASSIGNED'
        self.assigned_at = timezone.now()
        if user is not None:
            self.assigned_to = user
        if courier_name:
            self.courier_name = courier_name
        if tracking_number:
            self.tracking_number = tracking_number
        self.save()
        return self

    def mark_picked_up(self):
        self._guard()
        self.status = 'PICKED_UP'
        self.picked_up_at = timezone.now()
        self.save()
        return self

    def mark_in_transit(self):
        self._guard()
        self.status = 'IN_TRANSIT'
        if self.picked_up_at is None:
            self.picked_up_at = timezone.now()
        self.save()
        self._set_order_status('OUT_FOR_DELIVERY')
        return self

    def mark_delivered(self):
        self._guard()
        self.status = 'DELIVERED'
        self.delivered_at = timezone.now()
        self.save()

        order = self.order
        order.status = 'DELIVERED'
        order.delivered_at = self.delivered_at
        order.save(update_fields=['status', 'delivered_at', 'updated_at'])
        return self

    def mark_failed(self, reason=''):
        self._guard()
        self.status = 'FAILED'
        self.failed_at = timezone.now()
        self.failure_reason = reason
        self.save()
        return self

    def cancel(self, reason=''):
        if self.status == 'DELIVERED':
            raise ValueError('Cannot cancel a delivery that was already made')
        self.status = 'CANCELLED'
        self.cancelled_at = timezone.now()
        self.failure_reason = reason
        self.save()
        return self

    def _set_order_status(self, new_status):
        order = self.order
        if order.status != new_status:
            order.status = new_status
            order.save(update_fields=['status', 'updated_at'])

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def is_active(self):
        return self.status not in self.TERMINAL_STATUSES

    @property
    def is_free(self):
        return self.fee == Decimal('0.00')
