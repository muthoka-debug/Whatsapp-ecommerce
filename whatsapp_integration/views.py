import json
import logging
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsBusinessManagerOrOwner, IsBusinessMember
from customers.models import Customer
from orders.models import Order

from .client import WhatsAppAPIError
from .providers import get_client
from .messaging import (
    OutsideServiceWindow,
    send_to_customer,
    service_window_expires_at,
)
from .models import (
    WhatsAppChatbotFlow,
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppSetting,
    WhatsAppTemplate,
    WhatsAppWebhookLog,
)
from .serializers import (
    SendWhatsAppMessageSerializer,
    WhatsAppChatbotFlowSerializer,
    WhatsAppConversationSerializer,
    WhatsAppMessageSerializer,
    WhatsAppSettingSerializer,
    WhatsAppTemplateCreateSerializer,
    WhatsAppTemplateSerializer,
)
from .webhooks import verify_signature, verify_twilio_signature

logger = logging.getLogger(__name__)


def _from_unix_timestamp(value):
    """Convert a WhatsApp epoch-seconds string into an aware datetime.

    ``django.utils.timezone`` has no ``fromtimestamp``; calling it raised
    AttributeError on every inbound message and status update.
    """
    if value in (None, ''):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    except (TypeError, ValueError):
        logger.warning('Unparseable WhatsApp timestamp: %r', value)
        return None


class WhatsAppConversationViewSet(viewsets.ModelViewSet):
    """ViewSet for managing WhatsApp conversations"""
    permission_classes = [IsAuthenticated, IsBusinessMember]
    serializer_class = WhatsAppConversationSerializer
    queryset = WhatsAppConversation.objects.none()  # OpenAPI model resolution

    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return WhatsAppConversation.objects.none()

        queryset = WhatsAppConversation.objects.filter(business=business)

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        customer_id = self.request.query_params.get('customer_id')
        if customer_id:
            queryset = queryset.filter(customer_id=customer_id)

        return queryset

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        """Get all messages in a conversation"""
        conversation = self.get_object()
        messages = conversation.messages.all()
        serializer = WhatsAppMessageSerializer(messages, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        """Send a message in this conversation"""
        conversation = self.get_object()
        serializer = SendWhatsAppMessageSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        try:
            whatsapp_settings = WhatsAppSetting.objects.get(
                business=request.current_business
            )
        except WhatsAppSetting.DoesNotExist:
            return Response(
                {'error': 'WhatsApp not configured for this business'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content = data.get('content', '')
        try:
            delivery = send_to_customer(
                request.current_business,
                conversation.whatsapp_id,
                content,
                setting=whatsapp_settings,
                conversation=conversation,
            )
        except OutsideServiceWindow as exc:
            # The reply is late rather than broken, so this is a 400 the agent
            # can act on ("send an approved template"), not a gateway failure.
            return Response(
                {
                    'error': str(exc),
                    'service_window_expired_at': service_window_expires_at(
                        conversation
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except WhatsAppAPIError as exc:
            logger.error('Failed to send WhatsApp message: %s', exc)
            return Response(
                {'error': str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        message = WhatsAppMessage.objects.create(
            business=request.current_business,
            conversation=conversation,
            message_id=delivery.message_id or f'MSG-{timezone.now().timestamp()}',
            direction='OUTGOING',
            message_type='TEMPLATE' if delivery.used_template else data['message_type'],
            content=content,
            media_url=data.get('media_url') or None,
            sent_at=timezone.now(),
        )

        return Response(WhatsAppMessageSerializer(message).data)

    @action(detail=True, methods=['post'])
    def mark_resolved(self, request, pk=None):
        """Mark conversation as resolved"""
        conversation = self.get_object()
        conversation.status = 'RESOLVED'
        conversation.save()
        serializer = self.get_serializer(conversation)
        return Response(serializer.data)


class WhatsAppMessageViewSet(viewsets.ModelViewSet):
    """ViewSet for managing WhatsApp messages"""
    permission_classes = [IsAuthenticated, IsBusinessMember]
    serializer_class = WhatsAppMessageSerializer
    queryset = WhatsAppMessage.objects.none()  # OpenAPI model resolution

    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return WhatsAppMessage.objects.none()
        return WhatsAppMessage.objects.filter(business=business)


class WhatsAppTemplateViewSet(viewsets.ModelViewSet):
    """ViewSet for managing WhatsApp templates"""
    permission_classes = [IsAuthenticated, IsBusinessManagerOrOwner]
    queryset = WhatsAppTemplate.objects.none()  # OpenAPI model resolution

    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return WhatsAppTemplate.objects.none()
        return WhatsAppTemplate.objects.filter(business=business)

    def get_serializer_class(self):
        if self.action == 'create':
            return WhatsAppTemplateCreateSerializer
        return WhatsAppTemplateSerializer

    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)


class WhatsAppSettingViewSet(viewsets.ModelViewSet):
    """ViewSet for managing WhatsApp settings"""
    permission_classes = [IsAuthenticated, IsBusinessManagerOrOwner]
    serializer_class = WhatsAppSettingSerializer
    queryset = WhatsAppSetting.objects.none()  # OpenAPI model resolution

    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return WhatsAppSetting.objects.none()
        return WhatsAppSetting.objects.filter(business=business)

    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)


class WhatsAppChatbotFlowViewSet(viewsets.ModelViewSet):
    """ViewSet for managing chatbot flows"""
    permission_classes = [IsAuthenticated, IsBusinessManagerOrOwner]
    serializer_class = WhatsAppChatbotFlowSerializer
    queryset = WhatsAppChatbotFlow.objects.none()  # OpenAPI model resolution

    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return WhatsAppChatbotFlow.objects.none()
        return WhatsAppChatbotFlow.objects.filter(business=business)

    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)


def _digits(value):
    """Strip +, spaces and punctuation so stored and delivered numbers compare."""
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def _settings_for_metadata(metadata):
    """Find the business a webhook payload belongs to.

    Match on ``phone_number_id`` first: it is Meta's own stable identifier and
    is exactly the value ``setup_whatsapp`` stored. ``display_phone_number``
    arrives without a leading "+" while operators naturally save theirs with
    one, so that fallback compares digits only.
    """
    phone_number_id = metadata.get('phone_number_id')
    if phone_number_id:
        setting = WhatsAppSetting.objects.select_related('business').filter(
            phone_number_id=phone_number_id
        ).first()
        if setting:
            return setting

    wanted = _digits(metadata.get('display_phone_number'))
    if not wanted:
        return None
    for setting in WhatsAppSetting.objects.select_related('business').all():
        if _digits(setting.display_phone_number) == wanted:
            return setting
    return None


@extend_schema(exclude=True)
class WhatsAppWebhookView(APIView):
    """Webhook endpoint for WhatsApp Business API.

    Public by necessity, so every POST is authenticated by its
    X-Hub-Signature-256 HMAC before we act on it.

    Excluded from the OpenAPI schema: Meta calls this, no client of ours does,
    and its payload shape is defined by Meta rather than by us.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    # Meta delivers in bursts and retries; throttling it would drop real
    # customer messages. The HMAC signature check is the gate here.
    throttle_classes = []

    def get(self, request):
        """Verify webhook endpoint during Meta's subscription handshake."""
        verify_token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        if not verify_token:
            return Response('Verification failed', status=status.HTTP_403_FORBIDDEN)

        # The verify token identifies which business is being subscribed.
        if not WhatsAppSetting.objects.filter(
            webhook_verify_token=verify_token
        ).exists():
            logger.warning('Invalid webhook verify token')
            return Response('Verification failed', status=status.HTTP_403_FORBIDDEN)

        # Plain text, not a DRF Response: Meta compares the body byte for byte
        # against the challenge it sent. JSON rendering would return
        # "1158201444" with quotes, which never matches, and the only symptom is
        # "The URL couldn't be validated" in the Meta dashboard.
        return HttpResponse(challenge or '', content_type='text/plain')

    def post(self, request):
        """Handle incoming webhook events."""
        if not verify_signature(request):
            return Response(
                {'error': 'Invalid signature'}, status=status.HTTP_403_FORBIDDEN
            )

        try:
            data = request.data
            logger.debug('Received WhatsApp webhook: %s', json.dumps(data)[:2000])

            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value', {})

                    for message in value.get('messages', []):
                        self.process_message(message, value)

                    for status_update in value.get('statuses', []):
                        self.process_status_update(status_update)

            return Response({'status': 'success'}, status=status.HTTP_200_OK)
        except Exception:
            # Log the traceback, then still 200 so Meta does not hot-loop
            # retries on a payload we will never be able to parse.
            logger.exception('Error processing WhatsApp webhook')
            return Response({'status': 'error'}, status=status.HTTP_200_OK)

    def process_message(self, message, value):
        """Process an incoming WhatsApp message."""
        sender = message.get('from')
        message_id = message.get('id')
        message_type = (message.get('type') or 'text').upper()
        metadata = value.get('metadata', {})

        whatsapp_settings = _settings_for_metadata(metadata)
        if whatsapp_settings is None:
            logger.error(
                'No WhatsApp settings for phone_number_id %r / %r',
                metadata.get('phone_number_id'),
                metadata.get('display_phone_number'),
            )
            return

        business = whatsapp_settings.business

        # Meta retries deliveries; message ids are unique so skip replays.
        if WhatsAppMessage.objects.filter(message_id=message_id).exists():
            logger.debug('Skipping duplicate WhatsApp message %s', message_id)
            return

        try:
            with transaction.atomic():
                customer, _ = Customer.objects.get_or_create(
                    business=business,
                    phone=sender,
                    defaults={'name': f'Customer {sender}'},
                )

                conversation, _ = WhatsAppConversation.objects.get_or_create(
                    business=business,
                    whatsapp_id=sender,
                    defaults={
                        'customer': customer,
                        'phone_number': sender,
                        'customer_name': customer.name,
                        'status': 'ACTIVE',
                    },
                )

                if conversation.customer_id != customer.id:
                    conversation.customer = customer
                    conversation.save(update_fields=['customer'])

                content, media_url = self._extract_content(message)

                whatsapp_message = WhatsAppMessage.objects.create(
                    business=business,
                    conversation=conversation,
                    message_id=message_id,
                    direction='INCOMING',
                    message_type=message_type,
                    content=content,
                    media_url=media_url or None,
                    sent_at=_from_unix_timestamp(message.get('timestamp'))
                    or timezone.now(),
                    metadata=message,
                )

                WhatsAppWebhookLog.objects.create(
                    business=business,
                    event_type='MESSAGE',
                    payload={'message': message, 'value': value},
                    processed=True,
                    processed_at=timezone.now(),
                )
        except Exception as exc:
            logger.exception('Error processing WhatsApp message %s', message_id)
            WhatsAppWebhookLog.objects.create(
                business=business,
                event_type='MESSAGE',
                payload={'message': message, 'value': value},
                processed=False,
                error_message=str(exc),
            )
            return

        # Outside the transaction: an API failure here must not roll back the
        # message we already stored.
        self.process_with_chatbot(conversation, whatsapp_message, business)

    @staticmethod
    def _extract_content(message):
        """Pull the text body and media URL out of a message of any type."""
        message_type = message.get('type')
        payload = message.get(message_type) or {}

        if message_type == 'text':
            return payload.get('body', ''), ''
        if message_type == 'document':
            return payload.get('filename', ''), payload.get('link', '')
        if message_type in ('image', 'video', 'audio'):
            return payload.get('caption', ''), payload.get('link', '')
        if message_type == 'button':
            return payload.get('text', ''), ''
        if message_type == 'interactive':
            reply = payload.get('button_reply') or payload.get('list_reply') or {}
            return reply.get('title', ''), ''
        return '', ''

    def process_status_update(self, status_update):
        """Process a delivery/read receipt."""
        message_id = status_update.get('id')
        new_status = status_update.get('status')
        occurred_at = _from_unix_timestamp(status_update.get('timestamp'))

        try:
            message = WhatsAppMessage.objects.select_related('business').get(
                message_id=message_id
            )
        except WhatsAppMessage.DoesNotExist:
            logger.warning('Status update for unknown message %s', message_id)
            return

        if new_status == 'delivered':
            message.delivered_at = occurred_at or timezone.now()
            message.save(update_fields=['delivered_at'])
        elif new_status == 'read':
            message.read_at = occurred_at or timezone.now()
            message.save(update_fields=['read_at'])

        WhatsAppWebhookLog.objects.create(
            business=message.business,
            event_type='STATUS',
            payload=status_update,
            processed=True,
            processed_at=timezone.now(),
        )

    def process_with_chatbot(self, conversation, message, business):
        """Route an inbound message to a matching chatbot flow."""
        try:
            whatsapp_settings = WhatsAppSetting.objects.get(business=business)
        except WhatsAppSetting.DoesNotExist:
            return

        if not whatsapp_settings.ai_enabled:
            return

        content_lower = (message.content or '').lower()
        flows = list(
            WhatsAppChatbotFlow.objects.filter(business=business, is_active=True)
        )

        matching_flow = next(
            (
                flow
                for flow in flows
                if any(
                    keyword.lower() in content_lower
                    for keyword in (flow.trigger_keywords or [])
                )
            ),
            None,
        )

        if matching_flow is None:
            matching_flow = next((f for f in flows if f.fallback_flow), None)

        if matching_flow is not None:
            self.execute_chatbot_flow(
                conversation, matching_flow, message, business, whatsapp_settings
            )

    def execute_chatbot_flow(
        self, conversation, flow, message, business, whatsapp_settings
    ):
        """Execute the first step of a chatbot flow."""
        steps = flow.steps or []
        if not steps:
            return

        current_step = steps[0]
        response_text = current_step.get('message', '')
        if not response_text:
            return

        try:
            provider_message_id = get_client(whatsapp_settings).send_text(
                conversation.whatsapp_id, response_text
            )
        except WhatsAppAPIError as exc:
            logger.error('Chatbot reply failed: %s', exc)
            return

        WhatsAppMessage.objects.create(
            business=business,
            conversation=conversation,
            message_id=provider_message_id or f'AUTO-{timezone.now().timestamp()}',
            direction='OUTGOING',
            message_type='TEXT',
            content=response_text,
            sent_at=timezone.now(),
            is_ai_generated=True,
        )

        if current_step.get('action') == 'create_order':
            self.handle_order_creation(
                conversation, message.content, business, whatsapp_settings
            )

    def handle_order_creation(
        self, conversation, content, business, whatsapp_settings
    ):
        """Open an empty order for a conversation so staff can fill it in.

        Line-item parsing from free text is not implemented; the order is
        created as a PENDING placeholder and confirmed by staff.
        """
        try:
            order = Order.objects.create(
                business=business,
                customer=conversation.customer,
                source='WHATSAPP',
                status='PENDING',
                total_amount=0,
                notes=f'Opened from WhatsApp conversation: {content}'[:1000],
            )
        except Exception:
            logger.exception(
                'Error creating order from conversation %s', conversation.id
            )
            return

        try:
            get_client(whatsapp_settings).send_text(
                conversation.whatsapp_id,
                f"✅ Order #{order.order_number} has been created! "
                "We'll confirm shortly.",
            )
        except WhatsAppAPIError as exc:
            logger.error('Order confirmation message failed: %s', exc)


@extend_schema(exclude=True)
class TwilioWhatsAppWebhookView(APIView):
    """Webhook endpoint for WhatsApp messages arriving through Twilio.

    Twilio posts form-encoded fields, not Meta's nested JSON. Rather than a
    second copy of the message-handling logic, each payload is translated into
    the shape :class:`WhatsAppWebhookView` already processes, so inbound
    messages create the same customers, conversations and chatbot replies no
    matter which provider carried them.

    Public by necessity, so every POST is authenticated by its
    X-Twilio-Signature before we act on it.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    # Same reasoning as the Meta webhook: retries and bursts are normal, and the
    # signature check is the real gate.
    throttle_classes = []

    def post(self, request):
        setting = _twilio_setting_for(request.POST.get('To'))
        if setting is None:
            # Nothing to verify against, and nowhere to file the message.
            logger.error(
                'Twilio webhook for unknown sender number %r',
                request.POST.get('To'),
            )
            return Response(status=status.HTTP_403_FORBIDDEN)

        if not verify_twilio_signature(request, setting.access_token):
            return Response(
                {'error': 'Invalid signature'}, status=status.HTTP_403_FORBIDDEN
            )

        try:
            params = {key: request.POST[key] for key in request.POST}
            logger.debug('Received Twilio webhook: %s', json.dumps(params)[:2000])

            handler = WhatsAppWebhookView()

            if params.get('MessageStatus'):
                handler.process_status_update(_twilio_status_payload(params))
            elif params.get('MessageSid'):
                message, value = _twilio_message_payload(params, setting)
                handler.process_message(message, value)

            # Twilio accepts an empty 200 as "handled, send no reply".
            return HttpResponse(status=status.HTTP_204_NO_CONTENT)
        except Exception:
            # Log the traceback, then still 2xx so Twilio does not hot-loop
            # retries on a payload we will never be able to parse.
            logger.exception('Error processing Twilio webhook')
            return HttpResponse(status=status.HTTP_204_NO_CONTENT)


def _twilio_setting_for(to_address):
    """Find the business whose Twilio sender number received this webhook."""
    wanted = _digits(to_address)
    if not wanted:
        return None

    for setting in WhatsAppSetting.objects.select_related('business').filter(
        provider='TWILIO'
    ):
        if wanted in (
            _digits(setting.phone_number_id),
            _digits(setting.display_phone_number),
        ):
            return setting
    return None


def _twilio_status_payload(params):
    """Translate a Twilio status callback into Meta's status shape."""
    return {
        'id': params.get('MessageSid'),
        # Twilio uses the same words for the two statuses we record, but
        # capitalisation varies by API version.
        'status': (params.get('MessageStatus') or '').lower(),
        'recipient_id': _digits(params.get('To')),
        # Twilio sends no timestamp on status callbacks; process_status_update
        # falls back to now() when this is absent.
        'timestamp': '',
    }


def _twilio_message_payload(params, setting):
    """Translate an inbound Twilio message into Meta's ``(message, value)`` pair.

    Returns the two arguments ``WhatsAppWebhookView.process_message`` takes.
    """
    body = params.get('Body') or ''
    message = {
        'from': _digits(params.get('From')),
        'id': params.get('MessageSid'),
        # Twilio omits a send timestamp on inbound messages; process_message
        # falls back to now(), which is within a second of the truth here.
        'timestamp': '',
        'type': 'text',
        'text': {'body': body},
    }

    media_count = int(params.get('NumMedia') or 0)
    if media_count:
        # Twilio media URLs need the account's basic auth to fetch, unlike
        # Meta's, which need a bearer token — both are provider-scoped, so
        # anything rendering media has to go back through the provider anyway.
        media_url = params.get('MediaUrl0') or ''
        content_type = params.get('MediaContentType0') or ''
        media_kind = _twilio_media_kind(content_type)
        message['type'] = media_kind
        message[media_kind] = (
            {'filename': body, 'link': media_url}
            if media_kind == 'document'
            else {'caption': body, 'link': media_url}
        )

    value = {
        'metadata': {
            'phone_number_id': setting.phone_number_id,
            'display_phone_number': setting.display_phone_number,
        },
        'contacts': [{
            'profile': {'name': params.get('ProfileName') or ''},
            'wa_id': _digits(params.get('From')),
        }],
        'messages': [message],
    }
    return message, value


def _twilio_media_kind(content_type):
    """Map a MIME type onto the message types WhatsAppMessage records."""
    major = (content_type or '').split('/')[0]
    return major if major in ('image', 'video', 'audio') else 'document'
