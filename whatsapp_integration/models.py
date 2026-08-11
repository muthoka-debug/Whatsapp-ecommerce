from django.conf import settings
from django.db import models

from core.fields import EncryptedTextField
from core.models import TenantAwareModel
from customers.models import Customer

class WhatsAppConversation(TenantAwareModel):
    """WhatsApp conversation with a customer"""
    CONVERSATION_STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('RESOLVED', 'Resolved'),
        ('ARCHIVED', 'Archived'),
        ('BLOCKED', 'Blocked'),
    ]
    
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='whatsapp_conversations')
    whatsapp_id = models.CharField(max_length=255, unique=True)  # WhatsApp user ID
    phone_number = models.CharField(max_length=15)
    customer_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=CONVERSATION_STATUS_CHOICES, default='ACTIVE')
    last_message_at = models.DateTimeField(auto_now=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, 
                                   null=True, blank=True, related_name='assigned_conversations')
    tags = models.JSONField(default=list, blank=True)  # For categorizing conversations
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        ordering = ['-last_message_at']
        indexes = [
            models.Index(fields=['business', 'status']),
            models.Index(fields=['business', 'whatsapp_id']),
            models.Index(fields=['customer', 'status']),
        ]
    
    def __str__(self):
        return f"Conversation with {self.customer_name or self.phone_number} - {self.status}"


class WhatsAppMessage(TenantAwareModel):
    """Individual WhatsApp message"""
    MESSAGE_TYPE_CHOICES = [
        ('TEXT', 'Text'),
        ('IMAGE', 'Image'),
        ('DOCUMENT', 'Document'),
        ('AUDIO', 'Audio'),
        ('VIDEO', 'Video'),
        ('BUTTON', 'Button'),
        ('LIST', 'List'),
        ('TEMPLATE', 'Template'),
        ('INTERACTIVE', 'Interactive'),
    ]
    
    DIRECTION_CHOICES = [
        ('INCOMING', 'Incoming'),
        ('OUTGOING', 'Outgoing'),
    ]
    
    conversation = models.ForeignKey(WhatsAppConversation, on_delete=models.CASCADE, related_name='messages')
    message_id = models.CharField(max_length=255, unique=True)  # WhatsApp message ID
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPE_CHOICES, default='TEXT')
    content = models.TextField()
    media_url = models.URLField(max_length=500, blank=True, null=True)
    media_caption = models.TextField(blank=True)
    sent_at = models.DateTimeField()
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    is_ai_generated = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        ordering = ['sent_at']
        indexes = [
            models.Index(fields=['conversation', 'direction']),
            models.Index(fields=['message_id']),
            models.Index(fields=['sent_at']),
        ]
    
    def __str__(self):
        return f"{self.direction} - {self.message_type} - {self.sent_at}"


class WhatsAppTemplate(TenantAwareModel):
    """WhatsApp message templates for automated responses"""
    TEMPLATE_CATEGORY_CHOICES = [
        ('ORDER_CONFIRMATION', 'Order Confirmation'),
        ('ORDER_STATUS', 'Order Status Update'),
        ('PAYMENT_CONFIRMATION', 'Payment Confirmation'),
        ('DELIVERY_UPDATE', 'Delivery Update'),
        ('WELCOME', 'Welcome Message'),
        ('ABANDONED_CART', 'Abandoned Cart Recovery'),
        ('CUSTOM', 'Custom'),
    ]
    
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=50, choices=TEMPLATE_CATEGORY_CHOICES)
    template_id = models.CharField(max_length=255, blank=True)  # WhatsApp template ID
    language = models.CharField(max_length=10, default='en')
    subject = models.CharField(max_length=100)
    body = models.TextField()
    variables = models.JSONField(default=list, blank=True)  # List of variable names
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [['business', 'name']]
        ordering = ['category', 'name']
    
    def __str__(self):
        return f"{self.get_category_display()} - {self.name}"


class WhatsAppWebhookLog(TenantAwareModel):
    """Log of incoming webhook events from WhatsApp"""
    EVENT_TYPES = [
        ('MESSAGE', 'Message'),
        ('STATUS', 'Status Update'),
        ('DELIVERY', 'Delivery Report'),
        ('READ', 'Read Receipt'),
        ('TEMPLATE', 'Template Status'),
    ]
    
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['event_type', 'processed']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.event_type} - {self.created_at}"


class WhatsAppSetting(TenantAwareModel):
    """WhatsApp Business API settings for each business"""
    # WhatsApp Business API credentials.
    # access_token is encrypted at rest, see core.fields.EncryptedTextField.
    phone_number_id = models.CharField(max_length=100)
    business_account_id = models.CharField(max_length=100)
    access_token = EncryptedTextField()

    # Business settings
    display_phone_number = models.CharField(max_length=15, unique=True)
    display_name = models.CharField(max_length=100)
    about_text = models.TextField(blank=True)
    
    # Automation settings
    auto_reply_enabled = models.BooleanField(default=False)
    auto_reply_message = models.TextField(blank=True)
    auto_reply_template = models.ForeignKey(WhatsAppTemplate, on_delete=models.SET_NULL, 
                                           null=True, blank=True, related_name='+')
    
    # AI settings
    ai_enabled = models.BooleanField(default=False)
    ai_context_window = models.IntegerField(default=10)  # Number of messages to keep in context
    
    # Order automation
    auto_order_confirmation = models.BooleanField(default=True)
    order_status_updates = models.BooleanField(default=True)
    delivery_updates = models.BooleanField(default=True)
    
    # Webhook configuration.
    # The verify token is what Meta echoes back during webhook setup; it must be
    # unique so a token resolves to exactly one business.
    webhook_url = models.URLField(max_length=500, blank=True)
    webhook_verify_token = models.CharField(max_length=100, unique=True)

    # Status
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [['business']]
    
    def __str__(self):
        return f"WhatsApp Settings - {self.business.name}"


class WhatsAppChatbotFlow(TenantAwareModel):
    """Chatbot flow definitions for automated conversations"""
    FLOW_TYPE_CHOICES = [
        ('ORDER', 'Order Processing'),
        ('SUPPORT', 'Customer Support'),
        ('SALES', 'Sales'),
        ('FEEDBACK', 'Feedback Collection'),
        ('CUSTOM', 'Custom'),
    ]
    
    name = models.CharField(max_length=100)
    flow_type = models.CharField(max_length=20, choices=FLOW_TYPE_CHOICES)
    is_active = models.BooleanField(default=True)
    
    # Flow steps as JSON
    steps = models.JSONField()  # Array of step objects
    # Step format: {
    #   "step_id": "1",
    #   "trigger": "keyword",
    #   "message": "Hello! How can I help?",
    #   "options": ["Option 1", "Option 2"],
    #   "next_step": "2",
    #   "action": "create_order" | "fetch_order" | "support"
    # }
    
    trigger_keywords = models.JSONField(default=list, blank=True)  # Keywords that trigger this flow
    fallback_flow = models.BooleanField(default=False)  # Use as fallback for unknown messages
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [['business', 'name']]
    
    def __str__(self):
        return f"{self.get_flow_type_display()} - {self.name}"