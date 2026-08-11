"""Authentication for M-Pesa (Daraja) callbacks.

Safaricom does not sign callbacks, so the endpoints are protected two ways:

1. A shared secret carried in the callback URL we registered with Safaricom
   (``?token=...``). Without it, anyone who learns a CheckoutRequestID could
   mark a payment COMPLETED and flip an order to PAID.
2. An optional source-IP allowlist for Safaricom's published egress ranges.
"""

import ipaddress
import logging

from django.conf import settings

from core.utils import get_client_ip

logger = logging.getLogger(__name__)


def _token_ok(request):
    expected = getattr(settings, 'MPESA_CALLBACK_SECRET', '')

    if not expected:
        if settings.DEBUG:
            logger.warning(
                'MPESA_CALLBACK_SECRET is unset — accepting an unauthenticated '
                'M-Pesa callback because DEBUG is on. Set it before deploying.'
            )
            return True
        logger.error('MPESA_CALLBACK_SECRET is unset; rejecting callback.')
        return False

    import hmac

    provided = request.GET.get('token') or request.headers.get('X-Callback-Token', '')
    # Constant-time comparison so the secret cannot be recovered byte by byte.
    return hmac.compare_digest(str(provided), str(expected))


def _ip_ok(request):
    allowed = getattr(settings, 'MPESA_CALLBACK_ALLOWED_IPS', []) or []
    if not allowed:
        return True

    client_ip = get_client_ip(request)
    if not client_ip:
        return False

    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        logger.warning('Unparseable client IP on M-Pesa callback: %r', client_ip)
        return False

    for entry in allowed:
        entry = entry.strip()
        if not entry:
            continue
        try:
            if address in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            logger.warning('Invalid entry in MPESA_CALLBACK_ALLOWED_IPS: %r', entry)

    logger.warning('M-Pesa callback rejected from disallowed IP %s', client_ip)
    return False


def is_authentic_callback(request):
    """True when the callback carries the shared secret and a permitted IP."""
    return _token_ok(request) and _ip_ok(request)
