import logging

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import IsBusinessMember, IsBusinessStaffOrAbove
from payments.models import Payment

from .models import Order
from .serializers import OrderCreateSerializer, OrderSerializer

logger = logging.getLogger(__name__)


class OrderViewSet(viewsets.ModelViewSet):
    # IsBusinessMember scopes every action to the active tenant; without it the
    # viewset only checked that *someone* was logged in.
    permission_classes = [IsAuthenticated, IsBusinessMember]
    # Lets the OpenAPI generator resolve the model without a request;
    # get_queryset() is what serves data.
    queryset = Order.objects.none()

    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return Order.objects.none()

        queryset = Order.objects.filter(business=business, is_deleted=False)

        # Filter by status if provided
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            queryset = queryset.filter(placed_at__date__gte=start_date)
        if end_date:
            queryset = queryset.filter(placed_at__date__lte=end_date)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create':
            return OrderCreateSerializer
        return OrderSerializer
    
    def get_permissions(self):
        # Reading is open to any member; writing requires staff or above.
        if self.action in ('create', 'update', 'partial_update', 'destroy',
                           'update_status'):
            return [IsAuthenticated(), IsBusinessStaffOrAbove()]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user,
            business=self.request.current_business
        )

    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        order = self.get_object()
        new_status = request.data.get('status')

        if not new_status:
            return Response(
                {'error': 'Status is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if new_status not in dict(Order.ORDER_STATUS_CHOICES):
            return Response(
                {'error': 'Invalid status'},
                status=status.HTTP_400_BAD_REQUEST
            )

        old_status = order.status
        now = timezone.now()

        # Update status and relevant timestamps. These previously assigned a
        # DateTimeField *instance* instead of a datetime, writing a field object
        # into the column (and raising NameError, since `models` was never
        # imported here).
        order.status = new_status
        updated_fields = ['status', 'updated_at']

        if new_status == 'CONFIRMED':
            order.confirmed_at = now
            order.confirmed_by = request.user
            updated_fields += ['confirmed_at', 'confirmed_by']
        elif new_status == 'DELIVERED':
            order.delivered_at = now
            updated_fields.append('delivered_at')
        elif new_status == 'CANCELLED':
            order.cancelled_at = now
            updated_fields.append('cancelled_at')
            # Return reserved stock to inventory.
            for item in order.items.select_related('product'):
                item.product.increase_stock(item.quantity)

        order.save(update_fields=updated_fields)

        self._notify_status_change(order, old_status, new_status)

        serializer = self.get_serializer(order)
        return Response(serializer.data)

    @staticmethod
    def _notify_status_change(order, old_status, new_status):
        """Best-effort notification; a delivery failure must not fail the update."""
        from notifications.services import NotificationService

        try:
            NotificationService(order.business).send_order_status_update(
                order, old_status, new_status
            )
        except Exception:
            logger.exception(
                'Failed to send status notification for order %s', order.pk
            )
    
    @action(detail=True, methods=['get'])
    def payments(self, request, pk=None):
        order = self.get_object()
        payments = Payment.objects.filter(order=order)
        from payments.serializers import PaymentSerializer
        serializer = PaymentSerializer(payments, many=True)
        return Response(serializer.data)