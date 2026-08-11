from rest_framework import serializers

from orders.serializers import OrderSerializer

from .models import Payment, PaymentLog


class PaymentSerializer(serializers.ModelSerializer):
    method_display = serializers.CharField(source='get_method_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    order_details = OrderSerializer(source='order', read_only=True)

    # NOTE: `class Meta:6` used to sit here — valid Python (a one-line class
    # body of the expression `6`), so model/fields/read_only_fields were
    # attached to PaymentSerializer rather than to Meta. Meta then had no
    # `model`, and DRF raised at field-build time. It went unnoticed because an
    # empty result set never builds the child fields, so only non-empty payment
    # responses 500'd.
    class Meta:
        model = Payment
        fields = [
            'id', 'order', 'order_details', 'amount', 'method', 'method_display',
            'status', 'status_display', 'transaction_id', 'reference',
            'mpesa_receipt', 'mpesa_phone', 'metadata',
            'initiated_at', 'completed_at', 'refunded_at', 'processed_by'
        ]
        read_only_fields = ['transaction_id', 'initiated_at']


class PaymentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['order', 'amount', 'method', 'reference', 'mpesa_phone']

    def validate(self, data):
        # Ensure order belongs to current business
        order = data['order']
        if order.business != self.context['request'].current_business:
            raise serializers.ValidationError("Order does not belong to this business")

        # Check if order already has a pending payment
        if Payment.objects.filter(order=order, status__in=['PENDING', 'PROCESSING']).exists():
            raise serializers.ValidationError("Order already has a pending payment")

        return data

    def create(self, validated_data):
        validated_data['business'] = self.context['request'].current_business
        validated_data['processed_by'] = self.context['request'].user
        return Payment.objects.create(**validated_data)


class PaymentLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentLog
        fields = ['id', 'payment', 'action', 'status', 'message', 'metadata', 'created_at']
        read_only_fields = ['created_at']
