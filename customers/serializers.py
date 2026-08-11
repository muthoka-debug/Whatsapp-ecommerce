from decimal import Decimal

from rest_framework import serializers

from .models import Customer

class CustomerSerializer(serializers.ModelSerializer):
    total_orders = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()
    
    class Meta:
        model = Customer
        fields = ['id', 'name', 'phone', 'email', 'address', 'notes', 
                 'total_orders', 'total_spent', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_total_orders(self, obj) -> int:
        return obj.orders.filter(is_deleted=False).count()

    def get_total_spent(self, obj) -> Decimal:
        from django.db.models import Sum
        return obj.orders.filter(
            is_deleted=False,
            status='DELIVERED'
        ).aggregate(Sum('total_amount'))['total_amount__sum'] or 0

class CustomerCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ['name', 'phone', 'email', 'address', 'notes']