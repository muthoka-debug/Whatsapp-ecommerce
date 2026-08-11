"""Shared serializer building blocks for business-scoped (tenant) models.

This module previously held a stale, non-functional copy of the ``orders``
serializers — it did ``from .models import Order`` against ``core.models``,
which has no Order, so importing it raised ImportError. Nothing referenced it.
It now holds the mixins that were duplicated across the app serializers instead.
"""

from rest_framework import serializers


class BusinessScopedSerializerMixin:
    """Stamps the active business onto new objects and keeps it read-only.

    Prevents a client from creating or moving a record into another tenant by
    passing a ``business`` in the request body.
    """

    def _current_business(self):
        request = self.context.get('request')
        return getattr(request, 'current_business', None)

    def create(self, validated_data):
        business = self._current_business()
        if business is None:
            raise serializers.ValidationError(
                'A business context is required. Send the X-Business-ID header.'
            )
        validated_data['business'] = business
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # The tenant of an existing row is never reassignable.
        validated_data.pop('business', None)
        return super().update(instance, validated_data)


class AuditedSerializerMixin:
    """Populates created_by / updated_by from the request user.

    Only applies to models inheriting ``core.models.BaseModel``, which is where
    those columns live.
    """

    def create(self, validated_data):
        request = self.context.get('request')
        if request is not None and request.user.is_authenticated:
            validated_data.setdefault('created_by', request.user)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        if request is not None and request.user.is_authenticated:
            validated_data['updated_by'] = request.user
        return super().update(instance, validated_data)
