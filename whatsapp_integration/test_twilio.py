"""Tests for the Twilio WhatsApp provider.

Twilio differs from Meta in every mechanical detail — form-encoded webhooks
instead of JSON, HMAC-SHA1 over the URL instead of SHA256 over the body,
``whatsapp:+254...`` addresses instead of bare digits — while carrying exactly
the same conversations. These tests pin the translation between the two, since
a mistake in it looks like silence rather than an error.
"""

import base64
import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from customers.models import Customer

from .models import WhatsAppConversation, WhatsAppMessage, WhatsAppSetting
from .providers import get_client
from .tests import make_business
from .twilio_client import TwilioWhatsAppClient, to_whatsapp_address

SANDBOX = '+14155238886'
AUTH_TOKEN = 'twilio-auth-token'


def make_twilio_setting(business, **overrides):
    defaults = {
        'business': business,
        'provider': 'TWILIO',
        'phone_number_id': SANDBOX,
        'business_account_id': 'AC0123456789abcdef',
        'access_token': AUTH_TOKEN,
        'display_phone_number': SANDBOX,
        'display_name': business.name,
        'webhook_verify_token': f'twilio-token-{business.pk}',
    }
    defaults.update(overrides)
    return WhatsAppSetting.objects.create(**defaults)


def sign(url, params, auth_token=AUTH_TOKEN):
    """Reproduce Twilio's request signature for a form POST."""
    payload = url + ''.join(key + params[key] for key in sorted(params))
    digest = hmac.new(
        auth_token.encode(), msg=payload.encode(), digestmod=hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode()


class AddressNormalisationTests(TestCase):
    """``to_whatsapp_address`` sees numbers in every form the app stores."""

    def test_bare_digits_from_meta_style_storage(self):
        self.assertEqual(to_whatsapp_address('254712345678'), 'whatsapp:+254712345678')

    def test_plus_prefixed(self):
        self.assertEqual(to_whatsapp_address('+254712345678'), 'whatsapp:+254712345678')

    def test_already_a_twilio_address_is_not_doubled(self):
        self.assertEqual(
            to_whatsapp_address('whatsapp:+254712345678'), 'whatsapp:+254712345678'
        )


class ProviderSelectionTests(TestCase):
    """``get_client`` picks the client from the setting, not the call site."""

    @classmethod
    def setUpTestData(cls):
        cls.business = make_business(
            'Twilio Shop', username='twilio-owner', phone='0733456789',
        )

    def test_twilio_setting_returns_twilio_client(self):
        setting = make_twilio_setting(self.business)
        self.assertIsInstance(get_client(setting), TwilioWhatsAppClient)

    def test_meta_setting_returns_meta_client(self):
        from .client import WhatsAppClient

        setting = make_twilio_setting(
            self.business, provider='META', phone_number_id='999',
        )
        self.assertIsInstance(get_client(setting), WhatsAppClient)


class TwilioSendTests(TestCase):
    """What actually goes on the wire to Twilio."""

    @classmethod
    def setUpTestData(cls):
        cls.business = make_business(
            'Send Shop', username='twilio-send', phone='0744567890',
        )
        cls.setting = make_twilio_setting(cls.business)

    def test_send_text_posts_prefixed_addresses_and_returns_sid(self):
        with patch('whatsapp_integration.twilio_client.requests.post') as post:
            post.return_value.status_code = 201
            post.return_value.json.return_value = {'sid': 'SM123'}

            sid = TwilioWhatsAppClient(self.setting).send_text(
                '254712345678', 'Hello'
            )

        self.assertEqual(sid, 'SM123')
        sent = post.call_args.kwargs['data']
        self.assertEqual(sent['From'], f'whatsapp:{SANDBOX}')
        self.assertEqual(sent['To'], 'whatsapp:+254712345678')
        self.assertEqual(sent['Body'], 'Hello')
        # Basic auth, not a bearer token.
        self.assertEqual(
            post.call_args.kwargs['auth'],
            ('AC0123456789abcdef', AUTH_TOKEN),
        )

    def test_template_flattens_meta_components_into_content_variables(self):
        """Meta's positional body parameters become Twilio's numbered mapping."""
        components = [{
            'type': 'body',
            'parameters': [
                {'type': 'text', 'text': 'Ada'},
                {'type': 'text', 'text': 'ORD-9'},
            ],
        }]

        with patch('whatsapp_integration.twilio_client.requests.post') as post:
            post.return_value.status_code = 201
            post.return_value.json.return_value = {'sid': 'SM456'}

            TwilioWhatsAppClient(self.setting).send_template(
                '254712345678', 'HXabc123', components=components,
            )

        sent = post.call_args.kwargs['data']
        self.assertEqual(sent['ContentSid'], 'HXabc123')
        self.assertEqual(
            json.loads(sent['ContentVariables']), {'1': 'Ada', '2': 'ORD-9'}
        )

    def test_template_without_a_content_sid_is_refused_before_sending(self):
        """A Meta template name would be accepted by Twilio and then do nothing."""
        from .client import WhatsAppAPIError

        with patch('whatsapp_integration.twilio_client.requests.post') as post:
            with self.assertRaises(WhatsAppAPIError):
                TwilioWhatsAppClient(self.setting).send_template(
                    '254712345678', 'order_confirmation',
                )
        post.assert_not_called()

    def test_api_error_is_raised_with_twilio_detail(self):
        from .client import WhatsAppAPIError

        with patch('whatsapp_integration.twilio_client.requests.post') as post:
            post.return_value.status_code = 400
            post.return_value.text = '{"message": "not a valid number"}'

            with self.assertRaises(WhatsAppAPIError) as caught:
                TwilioWhatsAppClient(self.setting).send_text('bad', 'Hi')

        self.assertIn('not a valid number', str(caught.exception))


@override_settings(WHATSAPP_WEBHOOK_BASE_URL='https://example.ngrok-free.app')
class TwilioWebhookTests(TestCase):
    """Inbound Twilio webhooks become the same records a Meta payload makes."""

    @classmethod
    def setUpTestData(cls):
        cls.business = make_business(
            'Inbound Shop', username='twilio-inbound', phone='0755678901',
        )
        cls.setting = make_twilio_setting(cls.business)
        cls.url = reverse('whatsapp-webhook-twilio')
        cls.public_url = f'https://example.ngrok-free.app{cls.url}'

    def post_signed(self, params, auth_token=AUTH_TOKEN):
        return self.client.post(
            self.url,
            data=params,
            HTTP_X_TWILIO_SIGNATURE=sign(self.public_url, params, auth_token),
        )

    def inbound_params(self, **overrides):
        params = {
            'MessageSid': 'SM-inbound-1',
            'From': 'whatsapp:+254712345678',
            'To': f'whatsapp:{SANDBOX}',
            'Body': 'Do you have sugar?',
            'NumMedia': '0',
            'ProfileName': 'Ada',
        }
        params.update(overrides)
        return params

    def test_inbound_message_creates_customer_conversation_and_message(self):
        response = self.post_signed(self.inbound_params())

        self.assertEqual(response.status_code, 204)
        self.assertTrue(
            Customer.objects.filter(
                business=self.business, phone='254712345678'
            ).exists()
        )
        conversation = WhatsAppConversation.objects.get(business=self.business)
        self.assertEqual(conversation.whatsapp_id, '254712345678')

        message = WhatsAppMessage.objects.get(conversation=conversation)
        self.assertEqual(message.direction, 'INCOMING')
        self.assertEqual(message.message_type, 'TEXT')
        self.assertEqual(message.content, 'Do you have sugar?')
        self.assertEqual(message.message_id, 'SM-inbound-1')

    def test_media_message_records_type_and_url(self):
        self.post_signed(self.inbound_params(
            MessageSid='SM-inbound-media',
            NumMedia='1',
            MediaUrl0='https://api.twilio.com/media/ME123',
            MediaContentType0='image/jpeg',
            Body='my receipt',
        ))

        message = WhatsAppMessage.objects.get(message_id='SM-inbound-media')
        self.assertEqual(message.message_type, 'IMAGE')
        self.assertEqual(message.media_url, 'https://api.twilio.com/media/ME123')
        self.assertEqual(message.content, 'my receipt')

    def test_duplicate_delivery_is_ignored(self):
        """Twilio retries; MessageSid is stable, so the replay must not double up."""
        params = self.inbound_params(MessageSid='SM-retry')
        self.post_signed(params)
        self.post_signed(params)

        self.assertEqual(
            WhatsAppMessage.objects.filter(message_id='SM-retry').count(), 1
        )

    def test_status_callback_records_delivery(self):
        self.post_signed(self.inbound_params(MessageSid='SM-status-target'))
        self.post_signed({
            'MessageSid': 'SM-status-target',
            'MessageStatus': 'delivered',
            'To': f'whatsapp:{SANDBOX}',
            'From': 'whatsapp:+254712345678',
        })

        message = WhatsAppMessage.objects.get(message_id='SM-status-target')
        self.assertIsNotNone(message.delivered_at)

    @override_settings(DEBUG=False)
    def test_wrong_signature_is_rejected_and_stores_nothing(self):
        response = self.client.post(
            self.url,
            data=self.inbound_params(),
            HTTP_X_TWILIO_SIGNATURE='not-the-signature',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(WhatsAppMessage.objects.exists())

    @override_settings(DEBUG=False)
    def test_signature_from_a_different_auth_token_is_rejected(self):
        """Proves the check uses the receiving account's token, not any token."""
        response = self.post_signed(
            self.inbound_params(), auth_token='someone-elses-token'
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(WhatsAppMessage.objects.exists())

    @override_settings(DEBUG=False)
    def test_unknown_sender_number_is_rejected(self):
        """No setting means no auth token to verify against — refuse, don't guess."""
        response = self.client.post(
            self.url,
            data=self.inbound_params(To='whatsapp:+19998887777'),
            HTTP_X_TWILIO_SIGNATURE='irrelevant',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(WhatsAppMessage.objects.exists())
