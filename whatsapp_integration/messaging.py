"""Outbound delivery that respects Meta's 24-hour customer service window.

WhatsApp only accepts free-form messages within 24 hours of the customer's last
inbound message. Outside that window Meta rejects anything that is not a
pre-approved template, so every outbound path has to decide between the two.
Sending ``send_text`` unconditionally — which is what the notification and
chatbot code used to do — silently fails for exactly the customers who have not
messaged recently, which is most of them.

Call :func:`send_to_customer` instead of a provider client's ``send_text`` for
anything addressed to a customer. Staff-initiated replies go through it too, so
the dashboard can report the window rather than an opaque Meta error.
"""

import logging
from collections import namedtuple
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .client import WhatsAppAPIError
from .providers import get_client
from .models import (
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppSetting,
    WhatsAppTemplate,
)

logger = logging.getLogger(__name__)

#: Meta's customer service window, measured from the customer's last message.
CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)


class OutsideServiceWindow(WhatsAppAPIError):
    """The 24-hour window has closed and no approved template covers this send.

    A subclass of WhatsAppAPIError so existing ``except WhatsAppAPIError``
    handlers keep working; catch this specifically to tell the user *why* the
    message could not go out.
    """


#: What :func:`send_to_customer` returns. ``template_name`` is the name sent to
#: Meta, or None for a free-form text message.
Delivery = namedtuple('Delivery', 'message_id used_template template_name')


def get_conversation(business, phone):
    """Return the conversation with ``phone``, or None.

    Matches either stored form of the number: ``whatsapp_id`` is what Meta sends
    as the sender id, ``phone_number`` is what we copied off the customer.
    """
    return (
        WhatsAppConversation.objects.filter(business=business)
        .filter(Q(whatsapp_id=phone) | Q(phone_number=phone))
        .order_by('-last_message_at')
        .first()
    )


def last_inbound_at(conversation):
    """When the customer last messaged us, or None if they never have."""
    if conversation is None:
        return None
    return (
        WhatsAppMessage.objects.filter(
            conversation=conversation, direction='INCOMING'
        )
        .order_by('-sent_at')
        .values_list('sent_at', flat=True)
        .first()
    )


def service_window_expires_at(conversation):
    """When free-form messaging to this conversation stops being allowed."""
    opened_at = last_inbound_at(conversation)
    return opened_at + CUSTOMER_SERVICE_WINDOW if opened_at else None


def is_within_service_window(conversation):
    """True when we may still send free-form text to this conversation."""
    expires_at = service_window_expires_at(conversation)
    return expires_at is not None and timezone.now() < expires_at


def resolve_template(business, category):
    """Return the active template registered with Meta for ``category``, or None."""
    if not category:
        return None
    return (
        WhatsAppTemplate.objects.filter(
            business=business, category=category, is_active=True
        )
        .order_by('name')
        .first()
    )


def build_body_components(template, context):
    """Turn a template's declared variables into Meta body parameters.

    Meta takes body parameters positionally ({{1}}, {{2}}, ...), so the order of
    ``template.variables`` is what binds a context key to a placeholder. A
    variable missing from the context becomes an empty string rather than
    shifting every later parameter into the wrong slot.
    """
    variables = template.variables or []
    if not variables:
        return None

    context = context or {}
    return [{
        'type': 'body',
        'parameters': [
            {'type': 'text', 'text': str(context.get(name, ''))}
            for name in variables
        ],
    }]


def send_to_customer(
    business,
    phone,
    body,
    *,
    template_category=None,
    template_context=None,
    setting=None,
    conversation=None,
):
    """Deliver ``body`` to ``phone``, as text or as a template as the window allows.

    Args:
        business: the sending Business.
        phone: recipient in international format, digits only.
        body: the free-form text, used when the window is open.
        template_category: a ``WhatsAppTemplate`` category to fall back on once
            the window has closed. Without one, a closed window is an error.
        template_context: values for the template's declared variables.
        setting: the business's WhatsAppSetting, looked up when omitted.
        conversation: the conversation, looked up by ``phone`` when omitted.

    Returns:
        Delivery: the provider message id and which form was sent.

    Raises:
        OutsideServiceWindow: the window is closed and no template applies.
        WhatsAppAPIError: credentials are missing or Meta rejected the send.
    """
    if setting is None:
        try:
            setting = WhatsAppSetting.objects.get(business=business)
        except WhatsAppSetting.DoesNotExist as exc:
            raise WhatsAppAPIError(
                'WhatsApp is not configured for this business'
            ) from exc

    if conversation is None:
        conversation = get_conversation(business, phone)

    # Meta or Twilio, decided by the setting rather than by this call site.
    client = get_client(setting)

    if is_within_service_window(conversation):
        return Delivery(client.send_text(phone, body), False, None)

    template = resolve_template(business, template_category)
    if template is None:
        raise OutsideServiceWindow(
            'The 24-hour customer service window for '
            f'{phone} has closed and no active {template_category or "(none)"} '
            'template is available, so this message cannot be sent. Register an '
            'approved template with Meta and record it under that category.'
        )

    # template_id holds the name the template is registered under at Meta; the
    # local name is only a fallback for rows created before that was captured.
    template_name = template.template_id or template.name
    if not template.template_id:
        logger.warning(
            'WhatsApp template %r has no template_id; sending under its local '
            'name, which Meta will reject unless the two happen to match.',
            template.name,
        )

    message_id = client.send_template(
        phone,
        template_name,
        language=template.language or 'en',
        components=build_body_components(template, template_context),
    )
    return Delivery(message_id, True, template_name)
