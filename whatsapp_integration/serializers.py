from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import (
    WhatsAppConversation, WhatsAppMessage, WhatsAppTemplate,
    WhatsAppWebhookLog, WhatsAppSetting, WhatsAppChatbotFlow
)
from customers.serializers import CustomerSerializer

class WhatsAppMessageSerializer(serializers.ModelSerializer):
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)
    message_type_display = serializers.CharField(source='get_message_type_display', read_only=True)
    
    class Meta:
        model = WhatsAppMessage
        fields = [
            'id', 'conversation', 'message_id', 'direction', 'direction_display',
            'message_type', 'message_type_display', 'content', 'media_url',
            'media_caption', 'sent_at', 'delivered_at', 'read_at',
            'is_ai_generated', 'metadata'
        ]
        read_only_fields = ['id', 'sent_at']

class WhatsAppMessageCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppMessage
        fields = ['conversation', 'message_type', 'content', 'media_url', 'media_caption']

class WhatsAppConversationSerializer(serializers.ModelSerializer):
    customer_details = CustomerSerializer(source='customer', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    
    class Meta:
        model = WhatsAppConversation
        fields = [
            'id', 'customer', 'customer_details', 'whatsapp_id', 'phone_number',
            'customer_name', 'status', 'status_display', 'last_message_at',
            'assigned_to', 'tags', 'metadata', 'last_message', 'unread_count'
        ]
        read_only_fields = ['id', 'whatsapp_id', 'last_message_at']
    
    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_last_message(self, obj):
        last_msg = obj.messages.order_by('-sent_at').first()
        if last_msg:
            return WhatsAppMessageSerializer(last_msg).data
        return None

    def get_unread_count(self, obj) -> int:
        return obj.messages.filter(
            direction='INCOMING',
            read_at__isnull=True
        ).count()


class WhatsAppTemplateSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    
    class Meta:
        model = WhatsAppTemplate
        fields = [
            'id', 'name', 'category', 'category_display', 'template_id',
            'language', 'subject', 'body', 'variables', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

class WhatsAppTemplateCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppTemplate
        fields = ['name', 'category', 'language', 'subject', 'body', 'variables']
    
    def validate(self, data):
        # Validate variables are in body
        body = data.get('body', '')
        variables = data.get('variables', [])
        
        for var in variables:
            if f"{{{{{var}}}}}" not in body:
                raise serializers.ValidationError(
                    f"Variable '{var}' not found in body template"
                )
        
        return data


class WhatsAppSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppSetting
        fields = [
            'id', 'phone_number_id', 'business_account_id', 'access_token',
            'display_phone_number', 'display_name', 'about_text',
            'auto_reply_enabled', 'auto_reply_message', 'auto_reply_template',
            'ai_enabled', 'ai_context_window',
            'auto_order_confirmation', 'order_status_updates', 'delivery_updates',
            'webhook_url', 'webhook_verify_token',
            'is_active', 'is_verified', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'access_token': {'write_only': True},
            'webhook_verify_token': {'write_only': True},
        }


class WhatsAppChatbotFlowSerializer(serializers.ModelSerializer):
    flow_type_display = serializers.CharField(source='get_flow_type_display', read_only=True)
    
    class Meta:
        model = WhatsAppChatbotFlow
        fields = [
            'id', 'name', 'flow_type', 'flow_type_display', 'is_active',
            'steps', 'trigger_keywords', 'fallback_flow',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class WebhookPayloadSerializer(serializers.Serializer):
    """Serializer for incoming webhook payload"""
    object = serializers.CharField()
    entry = serializers.JSONField()


class SendWhatsAppMessageSerializer(serializers.Serializer):
    """Serializer for sending WhatsApp messages"""
    to = serializers.CharField(max_length=15)
    message_type = serializers.ChoiceField(choices=['TEXT', 'TEMPLATE'])
    content = serializers.CharField(required=False)
    template_id = serializers.CharField(required=False)
    template_variables = serializers.JSONField(required=False)
    media_url = serializers.URLField(required=False)
    caption = serializers.CharField(required=False)
    
    def validate(self, data):
        if data['message_type'] == 'TEXT' and not data.get('content'):
            raise serializers.ValidationError("Content is required for text messages")
        
        if data['message_type'] == 'TEMPLATE' and not data.get('template_id'):
            raise serializers.ValidationError("Template ID is required for template messages")
        
        return data