from django.core.validators import MinValueValidator
from django.db import models

from businesses.models import BusinessOwnedModel
from core.utils import generate_sku


class Category(BusinessOwnedModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subcategories',
    )
    icon = models.CharField(max_length=100, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['display_order', 'name']
        unique_together = [['business', 'name']]

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        """Soft delete — categories are referenced by historical products."""
        self.is_deleted = True
        self.save(update_fields=['is_deleted', 'updated_at'])


class Product(BusinessOwnedModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Pricing
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    compare_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text='Original price, shown struck through next to a sale price.',
    )
    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text='What the business pays. Used for margin reporting.',
    )

    # Inventory
    stock = models.IntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    sku = models.CharField(max_length=100, unique=True, blank=True)
    barcode = models.CharField(max_length=100, blank=True)

    # Presentation
    images = models.JSONField(default=list, blank=True)  # List of image URLs
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
    )
    tags = models.JSONField(default=list, blank=True)

    # Shipping
    weight = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text='Weight in kilograms.',
    )
    dimensions = models.JSONField(
        default=dict,
        blank=True,
        help_text='Free-form, e.g. {"length": 10, "width": 5, "height": 2}.',
    )

    # Status
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['business', 'is_active']),
            models.Index(fields=['business', 'is_featured']),
            models.Index(fields=['business', 'is_deleted']),
            models.Index(fields=['sku']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.sku:
            self.sku = generate_sku()
        super().save(*args, **kwargs)

    @property
    def in_stock(self):
        return self.stock > 0

    @property
    def is_low_stock(self):
        return self.stock <= self.low_stock_threshold

    def reduce_stock(self, quantity):
        """Atomically decrement stock. Raises ValueError if insufficient.

        Uses an F() expression so concurrent checkouts cannot oversell.
        """
        if quantity <= 0:
            raise ValueError('Quantity must be positive')

        updated = Product.objects.filter(
            pk=self.pk, stock__gte=quantity
        ).update(stock=models.F('stock') - quantity)

        if not updated:
            self.refresh_from_db(fields=['stock'])
            raise ValueError(
                f'Insufficient stock for {self.name}. Available: {self.stock}'
            )

        self.refresh_from_db(fields=['stock'])
        return self.stock

    def increase_stock(self, quantity):
        """Atomically increment stock (restock, or returning a cancelled order)."""
        if quantity <= 0:
            raise ValueError('Quantity must be positive')

        Product.objects.filter(pk=self.pk).update(
            stock=models.F('stock') + quantity
        )
        self.refresh_from_db(fields=['stock'])
        return self.stock

    def delete(self, using=None, keep_parents=False):
        """Soft delete — products are referenced by historical order items."""
        self.is_deleted = True
        self.is_active = False
        self.save(update_fields=['is_deleted', 'is_active', 'updated_at'])
