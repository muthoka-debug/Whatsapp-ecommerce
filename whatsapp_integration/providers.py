"""Provider selection for outbound WhatsApp sends.

Two providers reach the same WhatsApp network: Meta's Cloud API and Twilio.
Which one a business uses is a property of its ``WhatsAppSetting``, not of the
call site, so everything that sends a message asks for a client here rather
than naming a class.
"""

from .client import WhatsAppAPIError, WhatsAppClient
from .twilio_client import TwilioWhatsAppClient

PROVIDER_CLIENTS = {
    'META': WhatsAppClient,
    'TWILIO': TwilioWhatsAppClient,
}


def get_client(setting):
    """Return the send client for ``setting``'s provider.

    Args:
        setting: a ``WhatsAppSetting``.

    Raises:
        WhatsAppAPIError: the setting names a provider we have no client for.
    """
    # Rows created before the provider column existed default to META, which is
    # what they were.
    provider = (getattr(setting, 'provider', '') or 'META').upper()

    try:
        return PROVIDER_CLIENTS[provider](setting)
    except KeyError as exc:
        raise WhatsAppAPIError(f'Unknown WhatsApp provider {provider!r}') from exc
