from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Cart, CartItem, StorefrontSetting

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ['id', 'business', 'customer', 'session_id', 'total_items', 'subtotal', 'is_active', 'created_at']
    list_filter = ['business', 'is_active', 'created_at']
    search_fields = ['session_id', 'customer__name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    def total_items(self, obj):
        return obj.total_items
    total_items.short_description = 'Total Items'
    
    def subtotal(self, obj):
        return obj.subtotal
    subtotal.short_description = 'Subtotal'

@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'cart', 'product', 'quantity', 'price', 'total']
    list_filter = ['cart__business']
    search_fields = ['product__name', 'cart__session_id']
    readonly_fields = ['id', 'created_at', 'updated_at']

@admin.register(StorefrontSetting)
class StorefrontSettingAdmin(admin.ModelAdmin):
    list_display = ['business', 'theme', 'is_visible', 'created_at']
    list_filter = ['theme', 'is_visible', 'business']
    search_fields = ['business__name', 'banner_text']