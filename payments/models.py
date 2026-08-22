from django.conf import settings
from django.db import models
from django.utils import timezone

from businesses.models import BusinessOwnedModel
from core.fields import EncryptedTextField
from core.models import TenantAwareModel
from orders.models import Order

class Payment(BusinessOwnedModel):
    PAYMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PROCESSING', 'Processing'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    PAYMENT_METHOD_CHOICES = [
        ('CASH', 'Cash'),
        ('MPESA', 'M-Pesa'),
        ('CARD', 'Card'),
        ('BANK', 'Bank Transfer'),
        ('OTHER', 'Other'),
    ]
    
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='PENDING')
    transaction_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    reference = models.CharField(max_length=255, blank=True)
    
    # For M-Pesa specific fields
    mpesa_receipt = models.CharField(max_length=50, blank=True)
    mpesa_phone = models.CharField(max_length=15, blank=True)
    
    # Payment metadata (JSON field for additional data)
    metadata = models.JSONField(default=dict, blank=True)
    
    # Timestamps
    initiated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    
    # Who processed this payment
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='processed_payments')
    
    class Meta:
        ordering = ['-initiated_at']
        indexes = [
            models.Index(fields=['business', 'status']),
            models.Index(fields=['order', 'status']),
            models.Index(fields=['transaction_id']),
        ]
    
    def __str__(self):
        return f"Payment {self.transaction_id or self.id} - {self.amount} {self.method}"
    
    def complete_payment(self, transaction_id=None):
        self.status = 'COMPLETED'
        self.completed_at = timezone.now()
        if transaction_id:
            self.transaction_id = transaction_id

        # Update order status to PAID
        if self.order and self.order.status == 'PENDING':
            self.order.status = 'PAID'
            self.order.paid_at = timezone.now()
            self.order.save(update_fields=['status', 'paid_at', 'updated_at'])
            self._announce('ORDER_PAID', self.order, f'order-status:{self.order.pk}:PAID')

        self.save()
        self._announce('PAYMENT_COMPLETED', self, f'payment-completed:{self.pk}')

    def fail_payment(self, reason=''):
        """Mark this payment failed and announce it.

        Idempotent: providers retry failure callbacks too, and a second one
        must not re-alert staff.
        """
        if self.status == 'FAILED':
            return

        self.status = 'FAILED'
        if reason:
            self.metadata = {**(self.metadata or {}), 'failure_reason': reason}
        self.save(update_fields=['status', 'metadata', 'updated_at'])
        self._announce('PAYMENT_FAILED', self, f'payment-failed:{self.pk}', reason=reason)

    def _announce(self, event, subject, key, **payload):
        """Emit an automation event. Imported lazily to keep payments free of a
        hard dependency on the automation app, and never allowed to fail the
        payment write that preceded it."""
        from automation.services import emit

        emit(event, self.business, subject=subject, idempotency_key=key, **payload)


class PaymentLog(BusinessOwnedModel):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='logs')
    action = models.CharField(max_length=50)
    status = models.CharField(max_length=20)
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.payment} - {self.action} ({self.created_at})"


class MpesaSetting(TenantAwareModel):
    """M-Pesa API settings for each business"""
    # API Credentials — encrypted at rest, see core.fields.EncryptedTextField
    consumer_key = EncryptedTextField()
    consumer_secret = EncryptedTextField()
    passkey = EncryptedTextField()
    shortcode = models.CharField(max_length=100, default='174379')
    
    # URLs
    callback_url = models.URLField(max_length=500, blank=True)
    confirmation_url = models.URLField(max_length=500, blank=True)
    validation_url = models.URLField(max_length=500, blank=True)
    queue_timeout_url = models.URLField(max_length=500, blank=True)
    result_url = models.URLField(max_length=500, blank=True)
    
    # Settings
    environment = models.CharField(max_length=20, choices=[('sandbox', 'Sandbox'), ('production', 'Production')], default='sandbox')
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [['business']]
    
    def __str__(self):
        return f"M-Pesa Settings - {self.business.name}"


class MpesaTransaction(TenantAwareModel):
    """M-Pesa transaction record"""
    TRANSACTION_TYPE_CHOICES = [
        ('STK_PUSH', 'STK Push'),
        ('B2C', 'Business to Customer'),
        ('C2B', 'Customer to Business'),
        ('B2B', 'Business to Business'),
    ]
    
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PROCESSING', 'Processing'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
        ('TIMEOUT', 'Timeout'),
    ]
    
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='mpesa_transactions', null=True, blank=True)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES)
    checkout_request_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    transaction_id = models.CharField(max_length=100, unique=True, null=True, blank=True)  # M-Pesa transaction ID
    mpesa_receipt = models.CharField(max_length=100, null=True, blank=True)
    phone_number = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    account_reference = models.CharField(max_length=50)
    transaction_desc = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.TextField(blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    callback_payload = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['business', 'status']),
            models.Index(fields=['checkout_request_id']),
            models.Index(fields=['transaction_id']),
            models.Index(fields=['payment', 'status']),
        ]
    
    def __str__(self):
        return f"{self.transaction_type} - {self.phone_number} - {self.amount}"
    
    def complete(self, mpesa_receipt, result_code=0, result_desc='Success'):
        """Mark transaction as completed.

        Idempotent: Safaricom retries callbacks, so a second delivery for an
        already-completed transaction is a no-op rather than a duplicate
        order-status write.
        """
        if self.status == 'COMPLETED':
            return

        self.status = 'COMPLETED'
        self.mpesa_receipt = mpesa_receipt
        self.result_code = result_code
        self.result_desc = result_desc
        self.completed_at = timezone.now()
        self.save()
        
        # Update associated payment if exists
        if self.payment:
            self.payment.status = 'COMPLETED'
            self.payment.transaction_id = mpesa_receipt
            self.payment.mpesa_receipt = mpesa_receipt
            self.payment.completed_at = timezone.now()
            self.payment.save()

            # Update order status
            order = self.payment.order
            if order:
                order.status = 'PAID'
                order.paid_at = timezone.now()
                order.save()
                self.payment._announce(
                    'ORDER_PAID', order, f'order-status:{order.pk}:PAID'
                )

            self.payment._announce(
                'PAYMENT_COMPLETED',
                self.payment,
                f'payment-completed:{self.payment.pk}',
                mpesa_receipt=mpesa_receipt,
            )

    def fail(self, result_code, result_desc):
        """Mark transaction as failed.

        Idempotent for the same reason complete() is — Safaricom retries.
        """
        if self.status == 'FAILED':
            return

        self.status = 'FAILED'
        self.result_code = result_code
        self.result_desc = result_desc
        self.save()
        
        # Update associated payment. fail_payment() writes the row and emits
        # PAYMENT_FAILED, so staff hear about it without the callback thread
        # waiting on a notification.
        if self.payment:
            self.payment.fail_payment(reason=result_desc)