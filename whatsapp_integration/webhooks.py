"""Inbound WhatsApp webhook authentication.

Meta signs with HMAC-SHA256 over the raw body; Twilio signs with HMAC-SHA1
over the request URL plus its form fields. Both are checked before any inbound
payload is acted on.
"""

import base64
import hashlib
import hmac
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = 'X-Hub-Signature-256'
SIGNATURE_PREFIX = 'sha256='


def verify_signature(request):
    """Check Meta's X-Hub-Signature-256 header against the raw request body.

    Returns True when the request is authentic. Without this any caller could
    POST to the webhook and have us create customers, conversations and orders.

    If ``WHATSAPP_APP_SECRET`` is unset the check cannot run: allowed while
    DEBUG is on (so local testing with curl works), refused otherwise.
    """
    app_secret = getattr(settings, 'WHATSAPP_APP_SECRET', '')

    if not app_secret:
        if settings.DEBUG:
            logger.warning(
                'WHATSAPP_APP_SECRET is unset — accepting an unverified webhook '
                'because DEBUG is on. Set it before deploying.'
            )
            return True
        logger.error('WHATSAPP_APP_SECRET is unset; rejecting webhook.')
        return False

    header = request.headers.get(SIGNATURE_HEADER, '')
    if not header.startswith(SIGNATURE_PREFIX):
        logger.warning('Webhook rejected: missing or malformed %s', SIGNATURE_HEADER)
        return False

    expected = hmac.new(
        app_secret.encode(),
        msg=request.body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # compare_digest: constant time, so the comparison leaks no signature bytes.
    if not hmac.compare_digest(expected, header[len(SIGNATURE_PREFIX):]):
        logger.warning('Webhook rejected: signature mismatch')
        return False

    return True


TWILIO_SIGNATURE_HEADER = 'X-Twilio-Signature'


def _twilio_expected_signature(auth_token, url, post_params):
    """Build Twilio's signature for one candidate URL.

    Twilio signs the full request URL with every POST parameter appended to it,
    sorted by key, as ``key + value`` with no separators — then HMAC-SHA1 with
    the account's auth token, base64 encoded.
    """
    payload = url
    for key in sorted(post_params):
        payload += key + post_params[key]

    digest = hmac.new(
        auth_token.encode('utf-8'),
        msg=payload.encode('utf-8'),
        digestmod=hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode('utf-8')


def _twilio_candidate_urls(request):
    """The URLs Twilio may have signed, most likely first.

    Twilio signs the URL it called, which is the public https one. Behind ngrok
    or any TLS-terminating proxy Django sees plain http on an internal host, so
    ``build_absolute_uri`` reconstructs a URL that never matches. The configured
    public origin is tried first and the request's own view of itself second;
    both are HMAC-checked, so offering candidates weakens nothing.
    """
    path = request.get_full_path()
    candidates = []

    base = getattr(settings, 'WHATSAPP_WEBHOOK_BASE_URL', '') or ''
    if base:
        candidates.append(f"{base.rstrip('/')}{path}")

    absolute = request.build_absolute_uri()
    candidates.append(absolute)
    if absolute.startswith('http://'):
        candidates.append('https://' + absolute[len('http://'):])

    return candidates


def verify_twilio_signature(request, auth_token):
    """Check Twilio's X-Twilio-Signature against the request.

    Returns True when the request genuinely came from Twilio. Without this,
    anyone who learns the webhook URL could post messages as any customer.

    Args:
        request: the inbound Django request.
        auth_token: the Twilio Auth Token for the receiving account.

    If ``auth_token`` is missing the check cannot run: allowed while DEBUG is on
    so local curl testing works, refused otherwise — matching
    :func:`verify_signature`.
    """
    if not auth_token:
        if settings.DEBUG:
            logger.warning(
                'No Twilio auth token available — accepting an unverified '
                'webhook because DEBUG is on.'
            )
            return True
        logger.error('No Twilio auth token available; rejecting webhook.')
        return False

    header = request.headers.get(TWILIO_SIGNATURE_HEADER, '')
    if not header:
        logger.warning('Webhook rejected: missing %s', TWILIO_SIGNATURE_HEADER)
        return False

    post_params = {key: request.POST[key] for key in request.POST}

    for url in _twilio_candidate_urls(request):
        expected = _twilio_expected_signature(auth_token, url, post_params)
        # compare_digest: constant time, so the comparison leaks no bytes.
        if hmac.compare_digest(expected, header):
            return True

    logger.warning('Webhook rejected: Twilio signature mismatch')
    return False
