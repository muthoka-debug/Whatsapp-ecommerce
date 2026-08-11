from django.db import models

# Create your models here.
from django.db import models
from core.models import TenantAwareModel
from django.conf import settings
from orders.models import Order
from products.models import Product
from customers.models import Customer

class AnalyticsMetric(TenantAwareModel):
    """Store computed analytics metrics"""
    METRIC_TYPES = [
        ('DAILY_SALES', 'Daily Sales'),
        ('WEEKLY_SALES', 'Weekly Sales'),
        ('MONTHLY_SALES', 'Monthly Sales'),
        ('TOTAL_ORDERS', 'Total Orders'),
        ('TOTAL_CUSTOMERS', 'Total Customers'),
        ('REVENUE', 'Revenue'),
        ('AVG_ORDER_VALUE', 'Average Order Value'),
        ('CONVERSION_RATE', 'Conversion Rate'),
        ('CUSTOMER_RETENTION', 'Customer Retention'),
        ('TOP_PRODUCT', 'Top Product'),
        ('TOP_CATEGORY', 'Top Category'),
    ]
    
    PERIOD_CHOICES = [
        ('DAILY', 'Daily'),
        ('WEEKLY', 'Weekly'),
        ('MONTHLY', 'Monthly'),
        ('QUARTERLY', 'Quarterly'),
        ('YEARLY', 'Yearly'),
    ]
    
    metric_type = models.CharField(max_length=50, choices=METRIC_TYPES)
    period = models.CharField(max_length=20, choices=PERIOD_CHOICES, default='DAILY')
    period_date = models.DateField()
    value = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    string_value = models.CharField(max_length=500, null=True, blank=True)  # For non-numeric metrics
    json_value = models.JSONField(null=True, blank=True)  # For complex metrics
    metadata = models.JSONField(default=dict, blank=True)
    computed_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = [['business', 'metric_type', 'period', 'period_date']]
        ordering = ['-period_date']
        indexes = [
            models.Index(fields=['business', 'metric_type', 'period_date']),
            models.Index(fields=['business', 'period', 'period_date']),
        ]
    
    def __str__(self):
        return f"{self.get_metric_type_display()} - {self.period_date}"


class AnalyticsEvent(TenantAwareModel):
    """Track user events for analytics"""
    EVENT_TYPES = [
        ('PAGE_VIEW', 'Page View'),
        ('PRODUCT_VIEW', 'Product View'),
        ('ADD_TO_CART', 'Add to Cart'),
        ('REMOVE_FROM_CART', 'Remove from Cart'),
        ('CHECKOUT_STARTED', 'Checkout Started'),
        ('ORDER_PLACED', 'Order Placed'),
        ('PAYMENT_COMPLETED', 'Payment Completed'),
        ('SEARCH', 'Search'),
        ('WHATSAPP_MESSAGE', 'WhatsApp Message'),
        ('WHATSAPP_CONVERSATION_STARTED', 'WhatsApp Conversation Started'),
    ]
    
    event_type = models.CharField(max_length=50, choices=EVENT_TYPES)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    session_id = models.CharField(max_length=255, null=True, blank=True)
    url = models.URLField(max_length=500, blank=True)
    referrer = models.URLField(max_length=500, blank=True)
    user_agent = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['business', 'event_type', 'created_at']),
            models.Index(fields=['business', 'customer', 'created_at']),
            models.Index(fields=['business', 'session_id']),
        ]
    
    def __str__(self):
        return f"{self.get_event_type_display()} - {self.created_at}"


class SalesReport(TenantAwareModel):
    """Sales reports generated for businesses"""
    REPORT_TYPE_CHOICES = [
        ('DAILY', 'Daily Report'),
        ('WEEKLY', 'Weekly Report'),
        ('MONTHLY', 'Monthly Report'),
        ('QUARTERLY', 'Quarterly Report'),
        ('CUSTOM', 'Custom Report'),
    ]
    
    report_type = models.CharField(max_length=20, choices=REPORT_TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Summary metrics
    total_revenue = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_orders = models.IntegerField(default=0)
    total_customers = models.IntegerField(default=0)
    average_order_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    conversion_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    
    # Detailed data (JSON)
    orders_by_status = models.JSONField(default=dict)
    orders_by_source = models.JSONField(default=dict)
    payments_by_method = models.JSONField(default=dict)
    top_products = models.JSONField(default=list)
    top_customers = models.JSONField(default=list)
    revenue_by_day = models.JSONField(default=list)
    
    # File exports
    pdf_file = models.URLField(max_length=500, blank=True, null=True)
    excel_file = models.URLField(max_length=500, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['business', 'report_type', 'created_at']),
            models.Index(fields=['business', 'start_date', 'end_date']),
        ]
    
    def __str__(self):
        return f"{self.get_report_type_display()} - {self.start_date} to {self.end_date}"


class AIInsight(TenantAwareModel):
    """AI-generated insights and recommendations"""
    INSIGHT_TYPE_CHOICES = [
        ('SALES_TREND', 'Sales Trend'),
        ('CUSTOMER_BEHAVIOR', 'Customer Behavior'),
        ('PRODUCT_RECOMMENDATION', 'Product Recommendation'),
        ('PRICE_OPTIMIZATION', 'Price Optimization'),
        ('INVENTORY_ALERT', 'Inventory Alert'),
        ('CUSTOMER_SEGMENT', 'Customer Segment'),
        ('MARKETING_OPPORTUNITY', 'Marketing Opportunity'),
        ('RISK_ALERT', 'Risk Alert'),
        ('PERFORMANCE_ALERT', 'Performance Alert'),
    ]
    
    PRIORITY_CHOICES = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    ]
    
    insight_type = models.CharField(max_length=50, choices=INSIGHT_TYPE_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField()
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    data = models.JSONField(default=dict, blank=True)  # Supporting data for the insight
    is_read = models.BooleanField(default=False)
    is_actionable = models.BooleanField(default=False)
    action_url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['business', 'insight_type', 'priority']),
            models.Index(fields=['business', 'is_read']),
        ]
    
    def __str__(self):
        return f"{self.get_insight_type_display()} - {self.title}"