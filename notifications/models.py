from django.db import models

# Create your models here.
from django.db import models
from core.models import TenantAwareModel
from django.conf import settings

class Notification(TenantAwareModel):
    """System notification model"""
    CHANNEL_CHOICES = [
        ('EMAIL', 'Email'),
        ('SMS', 'SMS'),
        ('WHATSAPP', 'WhatsApp'),
        ('IN_APP', 'In-App'),
        ('PUSH', 'Push Notification'),
    ]
    
    PRIORITY_CHOICES = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    ]
    
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=255)
    message = models.TextField()
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default='IN_APP')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    is_read = models.BooleanField(default=False)
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['business', 'is_read']),
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['channel', 'is_sent']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.recipient.email}"


class NotificationTemplate(TenantAwareModel):
    """Notification templates for different events"""
    TEMPLATE_TYPE_CHOICES = [
        ('ORDER_CONFIRMATION', 'Order Confirmation'),
        ('ORDER_STATUS', 'Order Status Update'),
        ('PAYMENT_CONFIRMATION', 'Payment Confirmation'),
        ('DELIVERY_UPDATE', 'Delivery Update'),
        ('WELCOME', 'Welcome'),
        ('ABANDONED_CART', 'Abandoned Cart'),
        ('PROMOTION', 'Promotion'),
    ]
    
    name = models.CharField(max_length=100)
    template_type = models.CharField(max_length=50, choices=TEMPLATE_TYPE_CHOICES)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    variables = models.JSONField(default=list, blank=True)  # List of variable names
    channel = models.CharField(max_length=20, choices=Notification.CHANNEL_CHOICES, default='EMAIL')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [['business', 'name']]
    
    def __str__(self):
        return f"{self.get_template_type_display()} - {self.name}"
    
    def render(self, context):
        """Render template with context"""
        body = self.body
        for key, value in context.items():
            body = body.replace(f"{{{{{key}}}}}", str(value))
        return body