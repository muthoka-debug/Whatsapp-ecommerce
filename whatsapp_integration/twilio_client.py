"""Client for sending WhatsApp messages through Twilio.

Mirrors :class:`whatsapp_integration.client.WhatsAppClient` method for method,
so callers that go through :func:`whatsapp_integration.providers.get_client`
work against either provider without branching.

Twilio's REST API is used directly over ``requests`` rather than through the
``twilio`` SDK — the surface we need is three fields on one endpoint, and this
keeps the dependency list as it is.
"""

import json
import logging

import requests
from django.conf import settings

from .client import WhatsAppAPIError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
API_BASE_URL = 'https://api.twilio.com/2010-04-01'

#: Twilio addresses WhatsApp endpoints with this scheme prefix.
WHATSAPP_PREFIX = 'whatsapp:'


def to_whatsapp_address(number):
    """Normalise a phone number into Twilio's ``whatsapp:+2547...`` form.

    Accepts what either provider hands us: Meta reports senders as bare digits
    ("254712345678"), operators save theirs with a plus, and Twilio echoes back
    an already-prefixed address.
    """
    value = str(number or '').strip()
    if value.startswith(WHATSAPP_PREFIX):
        value = value[len(WHATSAPP_PREFIX):]
    value = value.strip()
    if not value.startswith('+'):
        value = '+' + value.lstrip('+')
    return f'{WHATSAPP_PREFIX}{value}'


class TwilioWhatsAppClient:
    """Sends WhatsApp messages for one business through Twilio.

    Args:
        setting: a ``WhatsAppSetting`` with ``provider='TWILIO'``, carrying the
            Account SID in ``business_account_id``, the Auth Token in
            ``access_token`` and the sender number in ``phone_number_id``.
    """

    def __init__(self, setting):
        self.setting = setting
        base = getattr(settings, 'TWILIO_API_BASE_URL', API_BASE_URL)
        self.base_url = base.rstrip('/')

    @property
    def account_sid(self):
        return self.setting.business_account_id

    @property
    def auth_token(self):
        return self.setting.access_token

    @property
    def sender(self):
        return to_whatsapp_address(
            self.setting.phone_number_id or self.setting.display_phone_number
        )

    def _post(self, data):
        if not self.auth_token or not self.account_sid:
            raise WhatsAppAPIError('Twilio credentials are not configured')

        url = f'{self.base_url}/Accounts/{self.account_sid}/Messages.json'
        payload = {'From': self.sender, **data}

        try:
            response = requests.post(
                url,
                data=payload,
                auth=(self.account_sid, self.auth_token),
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(f'Twilio request failed: {exc}') from exc

        if response.status_code >= 400:
            raise WhatsAppAPIError(
                f'Twilio API returned {response.status_code}: {response.text}'
            )

        return response.json()

    def send_text(self, to, body, preview_url=False):
        """Send a plain text message. Returns the Twilio message SID.

        ``preview_url`` is accepted for interface parity and ignored: Twilio
        always renders link previews and offers no switch for it.
        """
        result = self._post({
            'To': to_whatsapp_address(to),
            'Body': body,
        })
        return result.get('sid')

    def send_template(self, to, template_name, language='en', components=None):
        """Send a pre-approved template.

        Twilio fronts approved templates with Content SIDs (``HX...``) and takes
        variables as a numbered JSON object rather than Meta's positional
        component list, so the components built for Meta are flattened here.

        ``language`` is unused: a Twilio Content SID already identifies one
        specific language variant.
        """
        if not str(template_name or '').startswith('HX'):
            raise WhatsAppAPIError(
                f'Twilio needs a Content SID (HX...) to send a template, got '
                f'{template_name!r}. Register the template in Twilio Content '
                'Template Builder and store the SID as the template_id.'
            )

        data = {'To': to_whatsapp_address(to), 'ContentSid': template_name}
        variables = self._content_variables(components)
        if variables:
            data['ContentVariables'] = json.dumps(variables)

        return self._post(data).get('sid')

    @staticmethod
    def _content_variables(components):
        """Flatten Meta body components into Twilio's ``{"1": ...}`` mapping.

        Both providers number placeholders from 1 in the order given, so the
        positions carry over unchanged.
        """
        for component in components or []:
            if component.get('type') != 'body':
                continue
            parameters = component.get('parameters') or []
            return {
                str(index): parameter.get('text', '')
                for index, parameter in enumerate(parameters, start=1)
            }
        return {}

    def mark_read(self, message_id):
        """No-op: Twilio exposes no way to send a read receipt.

        Kept so callers can mark messages read without knowing the provider.
        """
        logger.debug('mark_read is not supported on Twilio; ignoring %s', message_id)
        return None
