from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import (
    WhatsAppConversation, WhatsAppMessage, WhatsAppTemplate,
    WhatsAppWebhookLog, WhatsAppSetting, WhatsAppChatbotFlow
)

@admin.register(WhatsAppConversation)
class WhatsAppConversationAdmin(admin.ModelAdmin):
    list_display = ['id', 'business', 'customer', 'phone_number', 'status', 'last_message_at']
    list_filter = ['business', 'status']
    search_fields = ['customer__name', 'phone_number', 'whatsapp_id']
    readonly_fields = ['id', 'created_at', 'updated_at', 'last_message_at']

@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'conversation', 'direction', 'message_type', 'content_preview', 'sent_at']
    list_filter = ['direction', 'message_type', 'is_ai_generated']
    search_fields = ['content', 'message_id']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    def content_preview(self, obj):
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content
    content_preview.short_description = 'Content'

@admin.register(WhatsAppTemplate)
class WhatsAppTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'business', 'category', 'language', 'is_active']
    list_filter = ['business', 'category', 'language', 'is_active']
    search_fields = ['name', 'subject', 'body']

@admin.register(WhatsAppSetting)
class WhatsAppSettingAdmin(admin.ModelAdmin):
    list_display = ['business', 'display_phone_number', 'is_active', 'is_verified']
    list_filter = ['business', 'is_active', 'is_verified']
    search_fields = ['display_phone_number', 'display_name']
    readonly_fields = ['created_at', 'updated_at']

@admin.register(WhatsAppChatbotFlow)
class WhatsAppChatbotFlowAdmin(admin.ModelAdmin):
    list_display = ['name', 'business', 'flow_type', 'is_active', 'fallback_flow']
    list_filter = ['business', 'flow_type', 'is_active', 'fallback_flow']
    search_fields = ['name', 'trigger_keywords']

@admin.register(WhatsAppWebhookLog)
class WhatsAppWebhookLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'event_type', 'business', 'processed', 'created_at']
    list_filter = ['event_type', 'processed']
    readonly_fields = ['id', 'created_at']