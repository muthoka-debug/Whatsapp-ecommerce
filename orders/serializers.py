from decimal import Decimal

from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from customers.serializers import CustomerSerializer
from products.models import Product
from products.serializers import ProductSerializer

from .models import Order, OrderItem

class OrderItemSerializer(serializers.ModelSerializer):
    product_details = ProductSerializer(source='product', read_only=True)
    subtotal = serializers.SerializerMethodField()
    
    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'product_details', 'quantity', 'price', 'total', 'subtotal', 'notes']
        read_only_fields = ['total']
    
    def get_subtotal(self, obj) -> Decimal:
        return obj.price * obj.quantity

class OrderItemCreateSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
    queryset=Product.objects.all()
)
    quantity = serializers.IntegerField(min_value=1)
    notes = serializers.CharField(required=False, allow_blank=True)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set product queryset dynamically
        request = self.context.get('request')
        if request and hasattr(request, 'current_business'):
            self.fields['product'].queryset = request.current_business.products.filter(is_active=True)

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    customer_details = CustomerSerializer(source='customer', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    source_display = serializers.CharField(source='get_source_display', read_only=True)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    confirmed_by_name = serializers.CharField(source='confirmed_by.username', read_only=True)
    # The delivery_address/phone/notes columns moved onto deliveries.Delivery.
    delivery = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer', 'customer_details', 'status', 'status_display',
            'source', 'source_display', 'total_amount', 'discount', 'tax', 'shipping_fee',
            'subtotal', 'notes', 'delivery',
            'placed_at', 'confirmed_at', 'paid_at', 'delivered_at', 'cancelled_at',
            'items', 'created_by', 'created_by_name', 'confirmed_by', 'confirmed_by_name',
            'business'
        ]
        read_only_fields = ['order_number', 'placed_at', 'subtotal', 'business']

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_delivery(self, obj):
        # Imported here to keep orders independent of deliveries at import time.
        from deliveries.serializers import DeliverySerializer

        delivery = getattr(obj, 'delivery', None)
        return DeliverySerializer(delivery).data if delivery else None

class DeliveryInputSerializer(serializers.Serializer):
    """Optional delivery block accepted when creating an order."""

    address = serializers.CharField(max_length=1000)
    phone = serializers.CharField(max_length=15)
    recipient_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    method = serializers.ChoiceField(
        choices=['COURIER', 'RIDER', 'PICKUP', 'POSTAL'], default='RIDER'
    )
    fee = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, min_value=0
    )
    scheduled_for = serializers.DateTimeField(required=False)


class OrderCreateSerializer(serializers.ModelSerializer):
    items = OrderItemCreateSerializer(many=True)
    delivery = DeliveryInputSerializer(required=False)

    class Meta:
        model = Order
        fields = [
            'customer', 'source', 'discount', 'tax', 'shipping_fee',
            'notes', 'delivery', 'items'
        ]
    
    def validate_customer(self, value):
        """Ensure customer belongs to the current business"""
        request = self.context.get('request')
        if request and hasattr(request, 'current_business'):
            if value.business != request.current_business:
                raise serializers.ValidationError("Customer does not belong to this business")
        return value
    
    def validate(self, data):
        """Validate all items have sufficient stock"""
        request = self.context.get('request')
        if not request or not hasattr(request, 'current_business'):
            raise serializers.ValidationError("Business context is required")
        
        business = request.current_business
        
        # Check stock for each item
        for item_data in data.get('items', []):
            product = item_data['product']
            quantity = item_data['quantity']
            
            # Ensure product belongs to business
            if product.business != business:
                raise serializers.ValidationError(
                    f"Product {product.name} does not belong to this business"
                )
            
            # Check stock
            if product.stock < quantity:
                raise serializers.ValidationError(
                    f"Insufficient stock for {product.name}. Available: {product.stock}"
                )
        
        return data
    
    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        delivery_data = validated_data.pop('delivery', None)
        request = self.context.get('request')

        # Set business and created_by
        validated_data['business'] = request.current_business
        validated_data['created_by'] = request.user

        # Create order
        order = Order.objects.create(**validated_data)

        for item_data in items_data:
            product = item_data['product']
            quantity = item_data['quantity']

            OrderItem.objects.create(
                order=order,
                business=order.business,
                product=product,
                quantity=quantity,
                price=product.price,  # Use current product price
                notes=item_data.get('notes', '')
            )

            # Atomic decrement — validate() checked stock, but two concurrent
            # orders can both pass that check, so the DB has the final say.
            try:
                product.reduce_stock(quantity)
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc

        if delivery_data is not None:
            self._create_delivery(order, delivery_data)

        # Folds tax, shipping and discount into total_amount. Assigning the bare
        # line-item sum here used to drop all three from the order's total.
        # (Delivery.save() already recalculates, but an order without a delivery
        # still needs this.)
        order.recalculate_totals()

        return order

    @staticmethod
    def _create_delivery(order, delivery_data):
        from deliveries.models import Delivery

        # When the delivery block carries no fee, keep whatever shipping_fee the
        # order was created with rather than silently zeroing it.
        fee = delivery_data.pop('fee', None)
        if fee is None:
            fee = order.shipping_fee

        Delivery.objects.create(
            business=order.business,
            order=order,
            fee=fee,
            recipient_name=(
                delivery_data.pop('recipient_name', '') or order.customer.name
            ),
            **delivery_data,
        )

class OrderStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.ORDER_STATUS_CHOICES)
    notes = serializers.CharField(required=False, allow_blank=True)

class OrderFilterSerializer(serializers.Serializer):
    status = serializers.CharField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    customer = serializers.IntegerField(required=False)
    min_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    max_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)