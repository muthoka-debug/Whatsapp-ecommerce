"""Inbound WhatsApp webhook authentication."""

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
