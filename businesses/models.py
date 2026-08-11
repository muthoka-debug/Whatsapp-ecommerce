from django.db import models
from django.conf import settings
from django.utils.text import slugify


class Business(models.Model):
    BUSINESS_TYPES = [
        ('RETAIL', 'Retail'),
        ('WHOLESALE', 'Wholesale'),
        ('RESTAURANT', 'Restaurant'),
        ('SERVICE', 'Service'),
        ('OTHER', 'Other'),
    ]

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    logo = models.URLField(max_length=500, blank=True, null=True)
    phone = models.CharField(max_length=15)
    email = models.EmailField()
    address = models.TextField()
    currency = models.CharField(max_length=10, default='KSh')
    business_type = models.CharField(
        max_length=20,
        choices=BUSINESS_TYPES,
        default='RETAIL'
    )

    # The user who owns this business
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='businesses_owned'
    )

    # WhatsApp automation toggles, read by notifications.services.NotificationService
    whatsapp_auto_order_confirmation = models.BooleanField(
        default=False,
        help_text='Send order confirmations to customers over WhatsApp.',
    )
    whatsapp_order_status_updates = models.BooleanField(
        default=False,
        help_text='Send order status changes to customers over WhatsApp.',
    )
    whatsapp_delivery_updates = models.BooleanField(
        default=False,
        help_text='Send delivery milestones to customers over WhatsApp.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class BusinessOwnedModel(models.Model):
    business = models.ForeignKey(
        'businesses.Business',
        on_delete=models.CASCADE,
        # Gives Business.products, .orders, .payments, ... matching the
        # convention used by core.models.TenantAwareModel.
        related_name='%(class)ss',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    @classmethod
    def get_for_business(cls, business):
        return cls.objects.filter(business=business)