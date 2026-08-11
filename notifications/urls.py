from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import NotificationTemplateViewSet, NotificationViewSet

router = DefaultRouter()
router.register(r'templates', NotificationTemplateViewSet, basename='notification-template')
router.register(r'', NotificationViewSet, basename='notification')

urlpatterns = [
    path('', include(router.urls)),
]
