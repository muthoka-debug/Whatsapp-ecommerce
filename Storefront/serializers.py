from rest_framework import serializers
from .models import Cart, CartItem, StorefrontSetting
from products.serializers import ProductSerializer
from orders.serializers import OrderCreateSerializer

class CartItemSerializer(serializers.ModelSerializer):
    product_details = ProductSerializer(source='product', read_only=True)
    total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    
    class Meta:
        model = CartItem
        fields = ['id', 'product', 'product_details', 'quantity', 'price', 'total']
        read_only_fields = ['price']

class CartItemCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)

class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    total_items = serializers.IntegerField(read_only=True)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    
    class Meta:
        model = Cart
        fields = ['id', 'customer', 'items', 'total_items', 'subtotal', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at', 'is_active']


class StorefrontSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = StorefrontSetting
        fields = [
            'theme', 'primary_color', 'secondary_color', 'logo', 
            'banner_image', 'banner_text', 'banner_subtext', 'is_visible',
            'featured_categories', 'featured_products', 'default_delivery_fee',
            'facebook_url', 'twitter_url', 'instagram_url', 'whatsapp_url',
            'return_policy', 'shipping_policy', 'privacy_policy', 'terms_conditions'
        ]


class CheckoutSerializer(serializers.Serializer):
    customer_name = serializers.CharField(max_length=255)
    customer_phone = serializers.CharField(max_length=15)
    customer_email = serializers.EmailField(required=False)
    delivery_address = serializers.CharField(max_length=500)
    delivery_phone = serializers.CharField(max_length=15)
    delivery_notes = serializers.CharField(required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(choices=['CASH', 'MPESA', 'CARD', 'BANK'])
    mpesa_phone = serializers.CharField(max_length=15, required=False)
    
    def validate(self, data):
        # Validate M-Pesa phone if method is MPESA
        if data.get('payment_method') == 'MPESA':
            if not data.get('mpesa_phone'):
                raise serializers.ValidationError({
                    'mpesa_phone': 'M-Pesa phone number is required for M-Pesa payments'
                })
            
            # Validate phone format
            import re
            phone = re.sub(r'\s+', '', data['mpesa_phone'])
            if not re.match(r'^(0|\+254)7\d{8}$', phone):
                raise serializers.ValidationError({
                    'mpesa_phone': 'Invalid phone number format. Use 07XXXXXXXX or +2547XXXXXXXX'
                })
        
        return data