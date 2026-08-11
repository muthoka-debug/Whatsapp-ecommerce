from rest_framework import viewsets, filters, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from django_filters.rest_framework import DjangoFilterBackend
from .models import Product, Category
from .serializers import ProductSerializer, CategorySerializer, ProductStockUpdateSerializer
from core.permissions import IsBusinessMember, IsBusinessManagerOrOwner, IsBusinessStaffOrAbove
from core.mixin import BusinessFilterMixin, AuditMixin

class CategoryViewSet(BusinessFilterMixin, AuditMixin, viewsets.ModelViewSet):
    """ViewSet for managing product categories"""
    permission_classes = [IsAuthenticated, IsBusinessMember]
    # For OpenAPI model/path-param resolution; get_queryset() serves the data.
    queryset = Category.objects.none()


    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return Category.objects.none()
        return Category.objects.filter(business=business, is_deleted=False)
    
    def get_serializer_class(self):
        return CategorySerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update'):
            return [IsAuthenticated(), IsBusinessStaffOrAbove()]
        if self.action == 'destroy':
            return [IsAuthenticated(), IsBusinessManagerOrOwner()]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)

class ProductViewSet(BusinessFilterMixin, AuditMixin, viewsets.ModelViewSet):
    """ViewSet for managing products"""
    permission_classes = [IsAuthenticated, IsBusinessMember]
    # For OpenAPI model/path-param resolution; get_queryset() serves the data.
    queryset = Product.objects.none()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'is_active', 'is_featured']
    search_fields = ['name', 'description', 'sku', 'barcode']
    ordering_fields = ['name', 'price', 'stock', 'created_at']
    
    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return Product.objects.none()
        return Product.objects.filter(business=business, is_deleted=False)
    
    def get_serializer_class(self):
        return ProductSerializer

    def get_permissions(self):
        # Role checks live here so a denial renders as 403 rather than the 500
        # a bare PermissionError produced.
        if self.action in ('create', 'update', 'partial_update'):
            return [IsAuthenticated(), IsBusinessStaffOrAbove()]
        if self.action in ('destroy', 'update_stock'):
            return [IsAuthenticated(), IsBusinessManagerOrOwner()]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)

    @action(detail=True, methods=['post'])
    def update_stock(self, request, pk=None):
        """Update product stock - Only managers and owners can do this"""
        product = self.get_object()
        serializer = ProductStockUpdateSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        quantity = serializer.validated_data['quantity']
        stock_action = serializer.validated_data['action']

        # add/subtract go through the model's F()-based helpers so two
        # concurrent adjustments cannot clobber each other.
        try:
            if stock_action == 'add':
                product.increase_stock(quantity)
            elif stock_action == 'subtract':
                product.reduce_stock(quantity)
            elif stock_action == 'set':
                product.stock = quantity
                product.save(update_fields=['stock', 'updated_at'])
        except ValueError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            'message': 'Stock updated successfully',
            'new_stock': product.stock,
            'product': ProductSerializer(product).data
        })