import logging

from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import (
    IsBusinessManagerOrOwner,
    IsBusinessMember,
    IsBusinessStaffOrAbove,
)

from .models import Delivery
from .serializers import (
    DeliveryAssignSerializer,
    DeliveryCreateSerializer,
    DeliveryFailSerializer,
    DeliverySerializer,
)

logger = logging.getLogger(__name__)


class DeliveryViewSet(viewsets.ModelViewSet):
    """Manage deliveries for the active business.

    Any member can read. Staff and above can create deliveries and drive the
    status transitions. Only managers and owners can delete one.
    """

    permission_classes = [IsAuthenticated, IsBusinessMember]
    # Declared so the OpenAPI generator can resolve the model and the `id` path
    # parameter type without a request. get_queryset() below is what actually
    # runs; this is never used to serve data.
    queryset = Delivery.objects.none()

    TRANSITION_ACTIONS = (
        'assign', 'mark_picked_up', 'mark_in_transit',
        'mark_delivered', 'mark_failed', 'cancel',
    )

    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return Delivery.objects.none()

        queryset = Delivery.objects.filter(business=business).select_related(
            'order', 'order__customer', 'assigned_to'
        )

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status__in=status_filter.split(','))

        method = self.request.query_params.get('method')
        if method:
            queryset = queryset.filter(method=method)

        order_id = self.request.query_params.get('order')
        if order_id:
            queryset = queryset.filter(order_id=order_id)

        # ?assigned_to=me is the rider's own queue.
        assigned_to = self.request.query_params.get('assigned_to')
        if assigned_to == 'me':
            queryset = queryset.filter(assigned_to=self.request.user)
        elif assigned_to:
            queryset = queryset.filter(assigned_to_id=assigned_to)

        if self.request.query_params.get('active') == 'true':
            queryset = queryset.exclude(status__in=Delivery.TERMINAL_STATUSES)

        return queryset

    def get_serializer_class(self):
        if self.action == 'create':
            return DeliveryCreateSerializer
        return DeliverySerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update') + self.TRANSITION_ACTIONS:
            return [IsAuthenticated(), IsBusinessStaffOrAbove()]
        if self.action == 'destroy':
            return [IsAuthenticated(), IsBusinessManagerOrOwner()]
        return super().get_permissions()

    def perform_create(self, serializer):
        delivery = serializer.save(business=self.request.current_business)
        self._notify(delivery, 'created')

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def _transition(self, handler, *args, **kwargs):
        """Run a model transition, turning its ValueError into a 400."""
        try:
            delivery = handler(*args, **kwargs)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        self._notify(delivery, delivery.status)
        return Response(DeliverySerializer(delivery).data)

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """Assign to an in-house rider and/or record courier details."""
        delivery = self.get_object()
        serializer = DeliveryAssignSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        rider = None

        if data.get('assigned_to'):
            rider = self._resolve_rider(data['assigned_to'])
            if rider is None:
                return Response(
                    {'error': 'Assignee is not an active member of this business'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return self._transition(
            delivery.assign,
            user=rider,
            courier_name=data.get('courier_name', ''),
            tracking_number=data.get('tracking_number', ''),
        )

    def _resolve_rider(self, user_id):
        """Only members of the active business may be assigned a delivery."""
        from accounts.models import UserBusinessMembership

        user_model = get_user_model()
        try:
            user = user_model.objects.get(pk=user_id)
        except user_model.DoesNotExist:
            return None

        is_member = UserBusinessMembership.objects.filter(
            user=user, business=self.request.current_business, is_active=True
        ).exists()
        return user if is_member else None

    @action(detail=True, methods=['post'])
    def mark_picked_up(self, request, pk=None):
        return self._transition(self.get_object().mark_picked_up)

    @action(detail=True, methods=['post'])
    def mark_in_transit(self, request, pk=None):
        """Sets the order to OUT_FOR_DELIVERY."""
        return self._transition(self.get_object().mark_in_transit)

    @action(detail=True, methods=['post'])
    def mark_delivered(self, request, pk=None):
        """Sets the order to DELIVERED."""
        return self._transition(self.get_object().mark_delivered)

    @action(detail=True, methods=['post'])
    def mark_failed(self, request, pk=None):
        delivery = self.get_object()
        serializer = DeliveryFailSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        return self._transition(
            delivery.mark_failed, reason=serializer.validated_data['reason']
        )

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        delivery = self.get_object()
        return self._transition(
            delivery.cancel, reason=request.data.get('reason', '')
        )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Counts per status, for the deliveries board."""
        from django.db.models import Count, Sum

        queryset = self.get_queryset()
        by_status = {
            row['status']: row['count']
            for row in queryset.values('status').annotate(count=Count('id'))
        }

        return Response({
            'by_status': {
                value: by_status.get(value, 0)
                for value, _ in Delivery.DELIVERY_STATUS_CHOICES
            },
            'active': queryset.exclude(
                status__in=Delivery.TERMINAL_STATUSES
            ).count(),
            'total': queryset.count(),
            'fees_collected': queryset.filter(status='DELIVERED').aggregate(
                total=Sum('fee')
            )['total'] or 0,
        })

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _notify(self, delivery, event):
        """Best-effort customer notification; never fails the request."""
        from notifications.services import NotificationService

        try:
            NotificationService(delivery.business).send_delivery_update(
                delivery, event
            )
        except Exception:
            logger.exception(
                'Failed to send delivery notification for delivery %s', delivery.pk
            )
