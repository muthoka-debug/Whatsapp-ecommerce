from rest_framework import serializers

from .models import Delivery


class DeliverySerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    method_display = serializers.CharField(source='get_method_display', read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    customer_name = serializers.CharField(source='order.customer.name', read_only=True)
    assigned_to_name = serializers.CharField(
        source='assigned_to.username', read_only=True
    )
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Delivery
        fields = [
            'id', 'order', 'order_number', 'customer_name',
            'recipient_name', 'address', 'phone', 'notes',
            'method', 'method_display', 'fee',
            'status', 'status_display', 'is_active',
            'assigned_to', 'assigned_to_name', 'courier_name', 'tracking_number',
            'scheduled_for', 'assigned_at', 'picked_up_at', 'delivered_at',
            'failed_at', 'cancelled_at', 'failure_reason',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'assigned_at', 'picked_up_at', 'delivered_at',
            'failed_at', 'cancelled_at', 'created_at', 'updated_at',
        ]


class DeliveryCreateSerializer(serializers.ModelSerializer):
    """Create a delivery for an order.

    Status is not accepted on create — a delivery always starts PENDING and
    moves through the transition endpoints, so the timestamps stay truthful.
    """

    class Meta:
        model = Delivery
        fields = [
            'order', 'recipient_name', 'address', 'phone', 'notes',
            'method', 'fee', 'assigned_to', 'courier_name', 'tracking_number',
            'scheduled_for',
        ]

    def validate_order(self, order):
        request = self.context.get('request')
        business = getattr(request, 'current_business', None)

        if business is None or order.business_id != business.id:
            raise serializers.ValidationError(
                'Order does not belong to this business'
            )

        if Delivery.objects.filter(order=order).exists():
            raise serializers.ValidationError(
                'This order already has a delivery'
            )

        if order.status == 'CANCELLED':
            raise serializers.ValidationError(
                'Cannot create a delivery for a cancelled order'
            )

        return order

    def validate_fee(self, fee):
        if fee < 0:
            raise serializers.ValidationError('Delivery fee cannot be negative')
        return fee

    def validate(self, data):
        # Fall back to the customer's own details when the caller omits them.
        order = data.get('order')
        if order is not None:
            if not data.get('recipient_name'):
                data['recipient_name'] = order.customer.name
            if not data.get('phone'):
                data['phone'] = order.customer.phone
            if not data.get('address'):
                data['address'] = order.customer.address
        return data


class DeliveryAssignSerializer(serializers.Serializer):
    assigned_to = serializers.IntegerField(required=False)
    courier_name = serializers.CharField(required=False, allow_blank=True)
    tracking_number = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        if not any(data.get(f) for f in
                   ('assigned_to', 'courier_name', 'tracking_number')):
            raise serializers.ValidationError(
                'Provide assigned_to, courier_name or tracking_number'
            )
        return data


class DeliveryFailSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000)
