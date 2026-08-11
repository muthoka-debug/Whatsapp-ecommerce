from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, models, transaction

from businesses.models import BusinessOwnedModel
from core.utils import generate_order_number
from customers.models import Customer
from products.models import Product

class Order(BusinessOwnedModel):
    ORDER_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('CONFIRMED', 'Confirmed'),
        ('PAID', 'Paid'),
        ('PROCESSING', 'Processing'),
        ('READY', 'Ready'),
        ('OUT_FOR_DELIVERY', 'Out for Delivery'),
        ('DELIVERED', 'Delivered'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    ORDER_SOURCE_CHOICES = [
        ('WHATSAPP', 'WhatsApp'),
        ('STOREFRONT', 'Storefront'),
        ('MANUAL', 'Manual'),
        ('PHONE', 'Phone'),
        ('INSTORE', 'In-Store'),
    ]
    
    order_number = models.CharField(max_length=50, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='orders')
    status = models.CharField(max_length=20, choices=ORDER_STATUS_CHOICES, default='PENDING')
    source = models.CharField(max_length=20, choices=ORDER_SOURCE_CHOICES, default='MANUAL')
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    is_deleted = models.BooleanField(default=False)

    # Delivery information lives on deliveries.Delivery (order.delivery), which
    # adds the status and fee the loose delivery_* columns here never had.

    # Timestamps
    placed_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    
    # Who processed this order
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, 
                                   null=True, blank=True, related_name='created_orders')
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='confirmed_orders')
    
    class Meta:
        ordering = ['-placed_at']
        indexes = [
            models.Index(fields=['business', 'status']),
            models.Index(fields=['business', 'order_number']),
            models.Index(fields=['customer', 'status']),
        ]
    
    def __str__(self):
        return f"Order #{self.order_number} - {self.customer.name}"
    
    def save(self, *args, **kwargs):
        if self.order_number:
            return super().save(*args, **kwargs)

        # placed_at is auto_now_add, so it is still None on the first save —
        # generate_order_number() stamps its own timestamp instead.
        #
        # order_number is UNIQUE, so retry on the (now very unlikely) collision
        # rather than surfacing an IntegrityError to the caller. Each attempt
        # runs in its own savepoint: a failed INSERT otherwise poisons the
        # surrounding atomic block.
        last_error = None
        for _ in range(5):
            self.order_number = generate_order_number()
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:
                if 'order_number' not in str(exc):
                    raise
                last_error = exc

        raise last_error

    def delete(self, using=None, keep_parents=False):
        """Soft delete — orders are financial records and payments PROTECT them."""
        self.is_deleted = True
        self.save(update_fields=['is_deleted', 'updated_at'])

    @property
    def subtotal(self):
        """Line-item total, i.e. total_amount before tax/shipping/discount.

        total_amount is the grand total the customer pays; keep the two in sync
        via recalculate_totals().
        """
        return self.total_amount - self.tax - self.shipping_fee + self.discount

    def recalculate_totals(self, save=True):
        """Recompute total_amount from the order's line items.

        total_amount is the grand total: line items + tax + shipping - discount.
        Callers used to assign the bare line-item sum, which silently dropped
        tax, shipping and discounts from every order.
        """
        line_total = sum(
            (item.total for item in self.items.all()),
            Decimal('0.00'),
        )
        self.total_amount = line_total + self.tax + self.shipping_fee - self.discount
        if save:
            self.save(update_fields=['total_amount', 'updated_at'])
        return self.total_amount


class OrderItem(BusinessOwnedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='order_items')
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)  # Price at time of order
    total = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['id']
    
    def __str__(self):
        return f"{self.product.name} x {self.quantity} - Order #{self.order.order_number}"
    
    def save(self, *args, **kwargs):
        self.total = self.price * self.quantity
        super().save(*args, **kwargs)