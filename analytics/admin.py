from django.contrib import admin

# Register your models here.

from .models import AnalyticsMetric, AnalyticsEvent, SalesReport, AIInsight

@admin.register(AnalyticsMetric)
class AnalyticsMetricAdmin(admin.ModelAdmin):
    list_display = ['metric_type', 'business', 'period', 'period_date', 'value']
    list_filter = ['business', 'metric_type', 'period']
    search_fields = ['metric_type']
    readonly_fields = ['id', 'computed_at']

@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ['event_type', 'business', 'user', 'customer', 'created_at']
    list_filter = ['business', 'event_type', 'created_at']
    search_fields = ['event_type', 'url']
    readonly_fields = ['id', 'created_at']

@admin.register(SalesReport)
class SalesReportAdmin(admin.ModelAdmin):
    list_display = ['report_type', 'business', 'start_date', 'end_date', 'total_revenue', 'total_orders']
    list_filter = ['business', 'report_type', 'created_at']
    search_fields = ['report_type']
    readonly_fields = ['id', 'created_at']

@admin.register(AIInsight)
class AIInsightAdmin(admin.ModelAdmin):
    list_display = ['insight_type', 'business', 'title', 'priority', 'is_read', 'created_at']
    list_filter = ['business', 'insight_type', 'priority', 'is_read']
    search_fields = ['title', 'description']
    readonly_fields = ['id', 'created_at']