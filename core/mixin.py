from rest_framework import status
from rest_framework.response import Response

class BusinessFilterMixin:
    """Mixin to automatically filter queryset by current business"""
    
    def get_queryset(self):
        queryset = super().get_queryset()
        business = self.request.current_business
        if business and hasattr(queryset.model, 'business'):
            return queryset.filter(business=business)
        return queryset

class AuditMixin:
    """Mixin to automatically set created_by and updated_by"""
    
    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user,
            business=self.request.current_business
        )
    
    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

class PermissionMixin:
    """Mixin to check permissions before actions"""
    
    def check_object_permissions(self, request, obj):
        # Add custom object-level permission checks here
        pass

class SoftDeleteMixin:
    """Mixin to handle soft delete"""
    
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if hasattr(instance, 'delete'):
            instance.delete()  # Uses soft delete if available
        else:
            instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)