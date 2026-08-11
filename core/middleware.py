"""Resolves the active business (tenant) for each request.

Every business-scoped view reads ``request.current_business``; role checks in
``core.permissions`` read ``request.current_business_membership``. Both
attributes are always set, so views never need hasattr() guards.

The tenant is selected with the ``X-Business-ID`` header. Anonymous visitors
(public storefront, cart) get ``current_business`` but never a membership, so
they can browse a shop without gaining any staff privileges.

Membership is resolved lazily rather than here. Under TokenAuthentication DRF
authenticates inside the view, so ``request.user`` is still AnonymousUser while
middleware runs — resolving membership at this point would deny every
token-authenticated request. ``core.permissions.get_membership()`` does it at
permission-check time, when the user is known.
"""

import logging

from businesses.models import Business

logger = logging.getLogger(__name__)

BUSINESS_HEADER = 'X-Business-ID'


class BusinessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.current_business = None
        # Placeholder so older code reading the attribute never AttributeErrors.
        # core.permissions.get_membership(request) is the supported accessor.
        request.current_business_membership = None

        business_id = request.headers.get(BUSINESS_HEADER)
        if business_id:
            request.current_business = self._get_business(business_id)

        return self.get_response(request)

    @staticmethod
    def _get_business(business_id):
        try:
            return Business.objects.get(pk=business_id)
        except (Business.DoesNotExist, ValueError, TypeError):
            # ValueError/TypeError: header carried a non-numeric id.
            logger.debug('Unknown %s: %r', BUSINESS_HEADER, business_id)
            return None
