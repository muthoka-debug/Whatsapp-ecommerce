from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DashboardViewSet, AnalyticsMetricViewSet,
    SalesReportViewSet, AIInsightViewSet
)

router = DefaultRouter()
router.register(r'dashboard', DashboardViewSet, basename='dashboard')
router.register(r'metrics', AnalyticsMetricViewSet, basename='analytics-metric')
router.register(r'reports', SalesReportViewSet, basename='sales-report')
router.register(r'insights', AIInsightViewSet, basename='ai-insight')

urlpatterns = [
    path('', include(router.urls)),
]