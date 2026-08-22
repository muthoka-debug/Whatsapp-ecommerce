from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    WhatsAppConversationViewSet, WhatsAppMessageViewSet,
    WhatsAppTemplateViewSet, WhatsAppSettingViewSet,
    WhatsAppChatbotFlowViewSet, WhatsAppWebhookView,
    TwilioWhatsAppWebhookView
)

router = DefaultRouter()
router.register(r'conversations', WhatsAppConversationViewSet, basename='whatsapp-conversation')
router.register(r'messages', WhatsAppMessageViewSet, basename='whatsapp-message')
router.register(r'templates', WhatsAppTemplateViewSet, basename='whatsapp-template')
router.register(r'settings', WhatsAppSettingViewSet, basename='whatsapp-setting')
router.register(r'flows', WhatsAppChatbotFlowViewSet, basename='whatsapp-flow')

urlpatterns = [
    path('', include(router.urls)),
    # Webhook endpoint (public)
    path('webhook/', WhatsAppWebhookView.as_view(), name='whatsapp-webhook'),
    # Twilio posts form-encoded fields to its own path; same processing behind it.
    path('webhook/twilio/', TwilioWhatsAppWebhookView.as_view(),
         name='whatsapp-webhook-twilio'),
]