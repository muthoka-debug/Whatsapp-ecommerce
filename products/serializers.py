from rest_framework import serializers
from .models import Product, Category

class CategorySerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source='parent.name', read_only=True)
    subcategory_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'parent', 'parent_name', 
                 'icon', 'display_order', 'subcategory_count', 'created_at']
        read_only_fields = ['id', 'created_at']
    
    def get_subcategory_count(self, obj) -> int:
        return obj.subcategories.filter(is_deleted=False).count()

class ProductSerializer(serializers.ModelSerializer):
    category_details = CategorySerializer(source='category', read_only=True)
    in_stock = serializers.BooleanField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = Product
        fields = ['id', 'name', 'description', 'price', 'compare_price', 'cost_price',
                 'stock', 'low_stock_threshold', 'sku', 'barcode', 'images',
                 'category', 'category_details', 'is_active', 'is_featured',
                 'weight', 'dimensions', 'tags', 'in_stock', 'is_low_stock',
                 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

class ProductStockUpdateSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)
    action = serializers.ChoiceField(choices=['add', 'subtract', 'set'])