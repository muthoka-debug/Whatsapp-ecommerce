from decimal import Decimal

from django.db import models

from core.models import TenantAwareModel
from customers.models import Customer
from products.models import Product

class Cart(TenantAwareModel):
    """Shopping cart model"""
    session_id = models.CharField(max_length=255, null=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['session_id', 'is_active']),
            models.Index(fields=['customer', 'is_active']),
            models.Index(fields=['expires_at']),
        ]
    
    def __str__(self):
        return f"Cart {self.id} - {self.customer or self.session_id}"
    
    @property
    def total_items(self):
        return self.items.aggregate(total=models.Sum('quantity'))['total'] or 0
    
    @property
    def subtotal(self):
        # output_field is required: price is Decimal and quantity is Integer, and
        # Django refuses to infer a type for the mixed-type expression.
        return self.items.aggregate(
            total=models.Sum(
                models.F('price') * models.F('quantity'),
                output_field=models.DecimalField(max_digits=12, decimal_places=2),
            )
        )['total'] or Decimal('0.00')
    
    def clear(self):
        """Clear all items from cart"""
        self.items.all().delete()
        self.is_active = False
        self.save()
    
    def merge_with_customer(self, customer):
        """Merge guest cart with customer cart"""
        # Get or create customer cart
        customer_cart, created = Cart.objects.get_or_create(
            business=self.business,
            customer=customer,
            is_active=True,
            defaults={'session_id': None}
        )
        
        if not created and customer_cart.id != self.id:
            # Merge items
            for item in self.items.all():
                # Check if product already exists in customer cart
                existing_item = customer_cart.items.filter(product=item.product).first()
                if existing_item:
                    existing_item.quantity += item.quantity
                    existing_item.save()
                else:
                    item.cart = customer_cart
                    item.save()
            
            # Delete guest cart
            self.delete()
        
        return customer_cart

class CartItem(TenantAwareModel):
    """Individual cart item"""
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)  # Price at add time
    
    class Meta:
        unique_together = [['cart', 'product']]
    
    def __str__(self):
        return f"{self.product.name} x {self.quantity} - {self.cart.id}"
    
    @property
    def total(self):
        return self.price * self.quantity
    
    def save(self, *args, **kwargs):
        # Set price from product if not set
        if not self.price:
            self.price = self.product.price
        super().save(*args, **kwargs)


class StorefrontSetting(TenantAwareModel):
    """Storefront customization settings"""
    theme = models.CharField(max_length=50, default='default')
    primary_color = models.CharField(max_length=20, default='#3B82F6')
    secondary_color = models.CharField(max_length=20, default='#1E293B')
    logo = models.URLField(max_length=500, blank=True, null=True)
    banner_image = models.URLField(max_length=500, blank=True, null=True)
    banner_text = models.TextField(blank=True)
    banner_subtext = models.TextField(blank=True)
    is_visible = models.BooleanField(default=True)
    featured_categories = models.JSONField(default=list, blank=True)  # List of category IDs
    featured_products = models.JSONField(default=list, blank=True)  # List of product IDs

    # Applied to the Delivery created at storefront checkout.
    default_delivery_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    
    # Social media links
    facebook_url = models.URLField(max_length=500, blank=True)
    twitter_url = models.URLField(max_length=500, blank=True)
    instagram_url = models.URLField(max_length=500, blank=True)
    whatsapp_url = models.URLField(max_length=500, blank=True)
    
    # Store policies
    return_policy = models.TextField(blank=True)
    shipping_policy = models.TextField(blank=True)
    privacy_policy = models.TextField(blank=True)
    terms_conditions = models.TextField(blank=True)
    
    def __str__(self):
        return f"Storefront Settings - {self.business.name}"
    
    class Meta:
        unique_together = [['business']]