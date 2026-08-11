from django.db import models
from django.contrib.auth import get_user_model
import uuid

User = get_user_model()

class BaseModel(models.Model):
    """
    Abstract base model that all models will inherit from.
    Provides common fields and methods.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL,
        null=True, 
        blank=True,
        related_name='%(class)s_created'
    )
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_updated'
    )
    
    class Meta:
        abstract = True
        ordering = ['-created_at']
    
    def save(self, *args, **kwargs):
        # Auto-set created_by and updated_by if not set
        if not self.created_by_id and hasattr(self, '_created_by'):
            self.created_by = self._created_by
        if hasattr(self, '_updated_by'):
            self.updated_by = self._updated_by
        super().save(*args, **kwargs)
    
    @classmethod
    def get_for_business(cls, business):
        """Base method for business filtering"""
        if hasattr(cls, 'business'):
            return cls.objects.filter(business=business)
        return cls.objects.none()


class TenantAwareModel(BaseModel):
    """
    Abstract model for all business-owned models.
    Adds business foreign key for multi-tenancy.
    """
    business = models.ForeignKey(
        'businesses.Business',
        on_delete=models.CASCADE,
        related_name='%(class)ss'  # Dynamic related_name
    )
    
    class Meta:
        abstract = True
        
    @classmethod
    def get_for_business(cls, business):
        """Get all objects for a specific business"""
        return cls.objects.filter(business=business)


class SoftDeleteModel(models.Model):
    """
    Abstract model for soft deletion.
    Instead of deleting, mark as deleted.
    """
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_deleted'
    )
    
    class Meta:
        abstract = True
    
    def delete(self, using=None, keep_parents=False):
        """Soft delete instead of hard delete"""
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()
    
    def hard_delete(self):
        """Actually delete from database"""
        super().delete()


class AuditLog(models.Model):
    """
    Audit log model for tracking changes across the system.
    This is a concrete model (not abstract).
    """
    ACTION_CHOICES = [
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        ('VIEW', 'View'),
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
        ('EXPORT', 'Export'),
        ('IMPORT', 'Import'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    business = models.ForeignKey('businesses.Business', on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=100)  # e.g., 'Order', 'Product'
    object_id = models.CharField(max_length=100, null=True, blank=True)
    object_repr = models.CharField(max_length=200, null=True, blank=True)
    changes = models.JSONField(default=dict, blank=True)  # What changed
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['business', 'timestamp']),
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['model_name', 'object_id']),
        ]
    
    def __str__(self):
        return f"{self.get_action_display()} - {self.model_name} - {self.timestamp}"


class SystemSetting(models.Model):
    """
    System-wide settings that can be configured.
    """
    SETTING_TYPES = [
        ('STRING', 'String'),
        ('INTEGER', 'Integer'),
        ('BOOLEAN', 'Boolean'),
        ('JSON', 'JSON'),
        ('DECIMAL', 'Decimal'),
    ]
    
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    type = models.CharField(max_length=20, choices=SETTING_TYPES, default='STRING')
    description = models.TextField(blank=True)
    is_public = models.BooleanField(default=False)  # Can be exposed via API
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['key']
    
    def __str__(self):
        return f"{self.key} = {self.value}"
    
    def get_value(self):
        """Get value with proper type casting"""
        if self.type == 'INTEGER':
            return int(self.value)
        elif self.type == 'BOOLEAN':
            return self.value.lower() in ['true', '1', 'yes']
        elif self.type == 'JSON':
            import json
            return json.loads(self.value)
        elif self.type == 'DECIMAL':
            return float(self.value)
        return self.value


class WebhookLog(models.Model):
    """
    Log for incoming webhooks (WhatsApp, M-Pesa, etc.)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey('businesses.Business', on_delete=models.SET_NULL, null=True, blank=True)
    source = models.CharField(max_length=50)  # 'whatsapp', 'mpesa', 'stripe', etc.
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()
    headers = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['source', 'processed']),
            models.Index(fields=['business', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.source} - {self.event_type} - {self.created_at}"