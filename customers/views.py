from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets
from rest_framework.permissions import IsAuthenticated

from core.mixin import AuditMixin, BusinessFilterMixin
from core.permissions import IsBusinessMember, IsBusinessStaffOrAbove

from .models import Customer
from .serializers import CustomerCreateSerializer, CustomerSerializer

class CustomerViewSet(BusinessFilterMixin, AuditMixin, viewsets.ModelViewSet):
    """ViewSet for managing customers"""
    permission_classes = [IsAuthenticated, IsBusinessMember]
    # For OpenAPI model/path-param resolution; get_queryset() serves the data.
    queryset = Customer.objects.none()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'phone', 'email']
    ordering_fields = ['name', 'created_at']
    ordering = ['-created_at']
    
    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return Customer.objects.none()
        return Customer.objects.filter(business=business)
    
    def get_serializer_class(self):
        if self.action == 'create':
            return CustomerCreateSerializer
        return CustomerSerializer

    def get_permissions(self):
        # Writes are restricted to staff and above. Enforcing this through the
        # permission class returns a proper 403; the previous inline check
        # raised a bare PermissionError, which surfaced as a 500.
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsAuthenticated(), IsBusinessStaffOrAbove()]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)