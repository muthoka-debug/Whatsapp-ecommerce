"""Thin client for the WhatsApp Business Cloud API.

Replaces the ``send_whatsapp_message`` stub that only wrote to the log, so
chatbot replies and notifications actually reach the customer.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15


class WhatsAppAPIError(Exception):
    """Raised when the WhatsApp Cloud API rejects a request."""


class WhatsAppClient:
    """Sends messages on behalf of one business.

    Args:
        setting: a ``WhatsAppSetting`` carrying the business's phone_number_id
            and (decrypted) access token.
    """

    def __init__(self, setting):
        self.setting = setting
        base = getattr(settings, 'WHATSAPP_API_BASE_URL', 'https://graph.facebook.com')
        version = getattr(settings, 'WHATSAPP_API_VERSION', 'v21.0')
        self.base_url = f"{base.rstrip('/')}/{version}"

    @property
    def _headers(self):
        return {
            'Authorization': f'Bearer {self.setting.access_token}',
            'Content-Type': 'application/json',
        }

    def _post(self, payload):
        if not self.setting.access_token or not self.setting.phone_number_id:
            raise WhatsAppAPIError('WhatsApp credentials are not configured')

        url = f'{self.base_url}/{self.setting.phone_number_id}/messages'
        try:
            response = requests.post(
                url, json=payload, headers=self._headers, timeout=DEFAULT_TIMEOUT
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(f'WhatsApp request failed: {exc}') from exc

        if response.status_code >= 400:
            raise WhatsAppAPIError(
                f'WhatsApp API returned {response.status_code}: {response.text}'
            )

        return response.json()

    def send_text(self, to, body, preview_url=False):
        """Send a plain text message. Returns the provider message id."""
        result = self._post({
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': to,
            'type': 'text',
            'text': {'preview_url': preview_url, 'body': body},
        })
        return self._first_message_id(result)

    def send_template(self, to, template_name, language='en', components=None):
        """Send a pre-approved template message."""
        template = {'name': template_name, 'language': {'code': language}}
        if components:
            template['components'] = components

        result = self._post({
            'messaging_product': 'whatsapp',
            'to': to,
            'type': 'template',
            'template': template,
        })
        return self._first_message_id(result)

    def mark_read(self, message_id):
        return self._post({
            'messaging_product': 'whatsapp',
            'status': 'read',
            'message_id': message_id,
        })

    @staticmethod
    def _first_message_id(result):
        messages = result.get('messages') or []
        return messages[0].get('id') if messages else None
