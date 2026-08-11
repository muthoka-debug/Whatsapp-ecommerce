from rest_framework import serializers
from .models import AnalyticsMetric, AnalyticsEvent, SalesReport, AIInsight

class AnalyticsMetricSerializer(serializers.ModelSerializer):
    metric_type_display = serializers.CharField(source='get_metric_type_display', read_only=True)
    period_display = serializers.CharField(source='get_period_display', read_only=True)
    
    class Meta:
        model = AnalyticsMetric
        fields = [
            'id', 'metric_type', 'metric_type_display', 'period', 'period_display',
            'period_date', 'value', 'string_value', 'json_value', 'metadata',
            'computed_at'
        ]
        read_only_fields = ['id', 'computed_at']


class AnalyticsEventSerializer(serializers.ModelSerializer):
    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    user_name = serializers.CharField(source='user.username', read_only=True)
    
    class Meta:
        model = AnalyticsEvent
        fields = [
            'id', 'event_type', 'event_type_display', 'user', 'user_name',
            'customer', 'customer_name', 'session_id', 'url', 'referrer',
            'user_agent', 'ip_address', 'metadata', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class SalesReportSerializer(serializers.ModelSerializer):
    report_type_display = serializers.CharField(source='get_report_type_display', read_only=True)
    generated_by_name = serializers.CharField(source='generated_by.username', read_only=True)
    
    class Meta:
        model = SalesReport
        fields = [
            'id', 'report_type', 'report_type_display', 'start_date', 'end_date',
            'generated_by', 'generated_by_name',
            'total_revenue', 'total_orders', 'total_customers',
            'average_order_value', 'conversion_rate',
            'orders_by_status', 'orders_by_source', 'payments_by_method',
            'top_products', 'top_customers', 'revenue_by_day',
            'pdf_file', 'excel_file', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class AIInsightSerializer(serializers.ModelSerializer):
    insight_type_display = serializers.CharField(source='get_insight_type_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    
    class Meta:
        model = AIInsight
        fields = [
            'id', 'insight_type', 'insight_type_display', 'title', 'description',
            'priority', 'priority_display', 'data', 'is_read', 'is_actionable',
            'action_url', 'created_at', 'read_at'
        ]
        read_only_fields = ['id', 'created_at']