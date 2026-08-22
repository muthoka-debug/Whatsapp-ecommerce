import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.permissions import IsBusinessOwner
from customers.models import Customer
from deliveries.models import Delivery
from orders.models import Order
from orders.serializers import OrderSerializer
from payments.models import Payment
from products.models import Category, Product
from products.serializers import ProductSerializer

from .models import Cart, CartItem, StorefrontSetting
from .serializers import (
    CartItemCreateSerializer,
    CartSerializer,
    CheckoutSerializer,
    StorefrontSettingSerializer,
)


class CartViewSet(viewsets.GenericViewSet):
    """ViewSet for managing shopping cart"""
    permission_classes = [AllowAny]
    serializer_class = CartSerializer
    # The cart is resolved from the session rather than looked up by pk, but
    # the OpenAPI generator still needs a model to point at.
    queryset = Cart.objects.none()
    
    def get_cart(self, request, business):
        """Get or create cart for the current session/customer"""
        cart_id = request.session.get('cart_id')
        cart = None

        # Try to get cart by session
        if cart_id:
            cart = Cart.objects.filter(
                id=cart_id,
                business=business,
                is_active=True
            ).first()

        # If user is authenticated, try to get their cart
        if request.user.is_authenticated and request.user.email:
            customer = Customer.objects.filter(
                email=request.user.email,
                business=business
            ).first()

            if customer:
                # Check if there's a guest cart to merge
                if cart:
                    cart = cart.merge_with_customer(customer)
                else:
                    cart, _ = Cart.objects.get_or_create(
                        business=business,
                        customer=customer,
                        is_active=True,
                        defaults={'session_id': None}
                    )
                request.session['cart_id'] = str(cart.id)

        # If no cart exists, create a new one
        if not cart:
            if not request.session.session_key:
                # An anonymous visitor has no session key until something is
                # written, and the cart id has to survive the next request.
                request.session.create()

            cart = Cart.objects.create(
                business=business,
                session_id=request.session.session_key or str(uuid.uuid4()),
                is_active=True,
                # timezone.timedelta does not exist - this used to raise
                # AttributeError for every first-time visitor.
                expires_at=timezone.now() + timedelta(days=7),
            )
            request.session['cart_id'] = str(cart.id)

        return cart
    
    def list(self, request, business_id=None):
        """Get current cart"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        cart = self.get_cart(request, business)
        serializer = self.get_serializer(cart)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def add_item(self, request):
        """Add item to cart"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = CartItemCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        product_id = serializer.validated_data['product_id']
        quantity = serializer.validated_data['quantity']
        
        # Get product
        try:
            product = Product.objects.get(
                id=product_id,
                business=business,
                is_active=True
            )
        except Product.DoesNotExist:
            return Response(
                {'error': 'Product not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check stock
        if product.stock < quantity:
            return Response(
                {'error': f'Insufficient stock. Available: {product.stock}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get cart
        cart = self.get_cart(request, business)
        
        # Add or update item
        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={
                'quantity': quantity,
                'price': product.price,
                'business': business
            }
        )
        
        if not created:
            # Check if total quantity exceeds stock
            if cart_item.quantity + quantity > product.stock:
                return Response(
                    {'error': f'Cannot add more. Available: {product.stock}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            cart_item.quantity += quantity
            cart_item.save()
        
        serializer = self.get_serializer(cart)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def update_item(self, request):
        """Update item quantity in cart"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        item_id = request.data.get('item_id')
        quantity = request.data.get('quantity')
        
        if not item_id or quantity is None:
            return Response(
                {'error': 'item_id and quantity are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            quantity = int(quantity)
            if quantity < 0:
                return Response(
                    {'error': 'Quantity must be positive'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except ValueError:
            return Response(
                {'error': 'Invalid quantity'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        cart = self.get_cart(request, business)
        
        try:
            cart_item = CartItem.objects.get(id=item_id, cart=cart)
        except CartItem.DoesNotExist:
            return Response(
                {'error': 'Item not found in cart'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        if quantity == 0:
            cart_item.delete()
        else:
            # Check stock
            if cart_item.product.stock < quantity:
                return Response(
                    {'error': f'Insufficient stock. Available: {cart_item.product.stock}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            cart_item.quantity = quantity
            cart_item.save()
        
        serializer = self.get_serializer(cart)
        return Response(serializer.data)
    
    @action(detail=False, methods=['delete'])
    def remove_item(self, request):
        """Remove item from cart"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        item_id = request.data.get('item_id')
        if not item_id:
            return Response(
                {'error': 'item_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        cart = self.get_cart(request, business)
        
        try:
            cart_item = CartItem.objects.get(id=item_id, cart=cart)
            cart_item.delete()
        except CartItem.DoesNotExist:
            return Response(
                {'error': 'Item not found in cart'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = self.get_serializer(cart)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def clear(self, request):
        """Clear entire cart"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        cart = self.get_cart(request, business)
        cart.clear()
        
        return Response({'message': 'Cart cleared successfully'})
    
    @action(detail=False, methods=['post'])
    def checkout(self, request):
        """Checkout - convert cart to order"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        cart = self.get_cart(request, business)
        
        # Check if cart has items
        if cart.items.count() == 0:
            return Response(
                {'error': 'Cart is empty'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate checkout data
        serializer = CheckoutSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        
        with transaction.atomic():
            # Create or get customer
            customer, created = Customer.objects.get_or_create(
                business=business,
                phone=data['customer_phone'],
                defaults={
                    'name': data['customer_name'],
                    'email': data.get('customer_email', ''),
                    'address': data.get('delivery_address', '')
                }
            )
            
            # If customer existed but name/email changed, update
            if not created:
                customer.name = data['customer_name']
                if data.get('customer_email'):
                    customer.email = data['customer_email']
                customer.save()
            
            # Create order. The delivery address/phone now live on the Delivery
            # record created below, not on the order itself.
            order = Order.objects.create(
                customer=customer,
                business=business,
                source='STOREFRONT',
                status='PENDING',
            )

            # Create order items from cart
            total = Decimal('0')
            for cart_item in cart.items.select_related('product'):
                order.items.create(
                    business=business,
                    product=cart_item.product,
                    quantity=cart_item.quantity,
                    price=cart_item.price,
                    total=cart_item.total
                )
                total += cart_item.total

                # Atomic decrement; raises if another checkout got there first.
                try:
                    cart_item.product.reduce_stock(cart_item.quantity)
                except ValueError as exc:
                    return Response(
                        {'error': str(exc)},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Fulfilment record for the order.
            storefront_setting = StorefrontSetting.objects.filter(
                business=business
            ).first()
            Delivery.objects.create(
                business=business,
                order=order,
                recipient_name=data['customer_name'],
                address=data['delivery_address'],
                phone=data['delivery_phone'],
                notes=data.get('delivery_notes', ''),
                fee=(
                    storefront_setting.default_delivery_fee
                    if storefront_setting else Decimal('0.00')
                ),
            )

            # Delivery.save() already syncs shipping_fee onto the order; this
            # folds in the line items.
            order.recalculate_totals()

            # Create payment for the order's grand total, which includes any
            # tax and shipping - not just the line-item sum.
            payment_data = {
                'order': order,
                'business': business,
                'amount': order.total_amount,
                'method': data['payment_method'],
                'status': 'PENDING'
            }

            if data['payment_method'] == 'MPESA':
                payment_data['mpesa_phone'] = data['mpesa_phone']

            payment = Payment.objects.create(**payment_data)

            # Clear cart
            cart.clear()

            # Announce inside the transaction: emit() defers the enqueue to
            # transaction.on_commit, so the worker only ever sees a committed
            # order, and a rollback here sends nothing.
            self._announce_order_created(business, order)

        return Response({
            'order': OrderSerializer(order).data,
            'payment': {
                'id': payment.id,
                'status': payment.status,
                'amount': payment.amount,
                'method': payment.method
            },
            'message': 'Order placed successfully'
        }, status=status.HTTP_201_CREATED)

    @staticmethod
    def _announce_order_created(business, order):
        from automation.events import Event
        from automation.services import emit

        emit(
            Event.ORDER_CREATED,
            business,
            subject=order,
            idempotency_key=f'order-created:{order.pk}',
            order_number=order.order_number,
            source='STOREFRONT',
        )


class StorefrontViewSet(viewsets.GenericViewSet):
    """Public storefront views - no authentication required"""
    permission_classes = [AllowAny]
    # Each action serializes a different model, so there is no single
    # serializer_class. Declared so the public endpoints still reach the schema.
    serializer_class = ProductSerializer
    queryset = Product.objects.none()


    def get_business(self, slug):
        """Get business by slug"""
        from businesses.models import Business
        return get_object_or_404(Business, slug=slug)
    
    @action(detail=False, methods=['get'], url_path='(?P<slug>[^/.]+)')
    def info(self, request, slug=None):
        """Public shop header: identity plus theme.

        The cart endpoints are business-scoped via the X-Business-ID header, so
        a visitor arriving at /shop/<slug>/ needs the id that goes with the slug
        before it can do anything. This is the only place that mapping is public.
        """
        business = self.get_business(slug)
        setting, _ = StorefrontSetting.objects.get_or_create(business=business)

        if not setting.is_visible:
            return Response(
                {'error': 'This storefront is not currently available'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            'business': {
                'id': business.id,
                'name': business.name,
                'slug': business.slug,
                'currency': business.currency,
                'phone': business.phone,
                'email': business.email,
                'address': business.address,
                'logo': business.logo,
            },
            'settings': StorefrontSettingSerializer(setting).data,
        })

    @action(detail=False, methods=['get'], url_path='(?P<slug>[^/.]+)/products')
    def products(self, request, slug=None):
        """Get products for a business storefront"""
        business = self.get_business(slug)
        
        # Get query params
        category_id = request.query_params.get('category')
        search = request.query_params.get('search')
        is_featured = request.query_params.get('featured') == 'true'
        
        products = Product.objects.filter(
            business=business,
            is_active=True,
            is_deleted=False
        )
        
        if category_id:
            products = products.filter(category_id=category_id)
        
        if search:
            products = products.filter(
                models.Q(name__icontains=search) |
                models.Q(description__icontains=search)
            )
        
        if is_featured:
            products = products.filter(is_featured=True)
        
        # Paginate
        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        paginator.page_size = 20
        result_page = paginator.paginate_queryset(products, request)
        
        serializer = ProductSerializer(result_page, many=True)
        return paginator.get_paginated_response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='(?P<slug>[^/.]+)/categories')
    def categories(self, request, slug=None):
        """Get categories for a business storefront"""
        business = self.get_business(slug)
        
        categories = Category.objects.filter(
            business=business,
            is_deleted=False
        ).filter(
            models.Q(parent__isnull=True) | models.Q(parent__is_deleted=False)
        )
        
        from products.serializers import CategorySerializer
        serializer = CategorySerializer(categories, many=True)
        return Response(serializer.data)
    
    # NOTE: must not be named `settings` - that shadows APIView.settings, which
    # DRF reads for EXCEPTION_HANDLER/FORMAT_SUFFIX_KWARG, and every action on
    # this viewset then fails with "'function' object has no attribute ...".
    @action(detail=False, methods=['get'], url_path='(?P<slug>[^/.]+)/settings')
    def store_settings(self, request, slug=None):
        """Get storefront settings"""
        business = self.get_business(slug)

        storefront_setting, _ = StorefrontSetting.objects.get_or_create(
            business=business
        )

        serializer = StorefrontSettingSerializer(storefront_setting)
        return Response(serializer.data)


class StorefrontSettingViewSet(viewsets.ModelViewSet):
    """Storefront settings management - authenticated"""
    permission_classes = [IsAuthenticated, IsBusinessOwner]
    serializer_class = StorefrontSettingSerializer
    queryset = StorefrontSetting.objects.none()  # OpenAPI model resolution
    
    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return StorefrontSetting.objects.none()
        return StorefrontSetting.objects.filter(business=business)
    
    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)