from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import IsBusinessManagerOrOwner, IsBusinessMember

from .models import Notification, NotificationTemplate
from .serializers import NotificationSerializer, NotificationTemplateSerializer


class NotificationViewSet(viewsets.ModelViewSet):
    """The signed-in user's notifications for the active business."""

    permission_classes = [IsAuthenticated, IsBusinessMember]
    serializer_class = NotificationSerializer
    queryset = Notification.objects.none()  # OpenAPI model resolution
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return Notification.objects.none()

        # Scoped to the caller: notifications are personal, not business-wide.
        queryset = Notification.objects.filter(
            business=business, recipient=self.request.user
        )

        channel = self.request.query_params.get('channel')
        if channel:
            queryset = queryset.filter(channel=channel)

        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)

        unread = self.request.query_params.get('unread')
        if unread == 'true':
            queryset = queryset.filter(is_read=False)

        return queryset

    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        return Response({'unread': self.get_queryset().filter(is_read=False).count()})

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=['is_read', 'read_at'])
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        updated = self.get_queryset().filter(is_read=False).update(
            is_read=True, read_at=timezone.now()
        )
        return Response({'marked_read': updated}, status=status.HTTP_200_OK)


class NotificationTemplateViewSet(viewsets.ModelViewSet):
    """Message templates. Managing them is a manager/owner action."""

    permission_classes = [IsAuthenticated, IsBusinessManagerOrOwner]
    serializer_class = NotificationTemplateSerializer
    queryset = NotificationTemplate.objects.none()  # OpenAPI model resolution

    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return NotificationTemplate.objects.none()

        queryset = NotificationTemplate.objects.filter(business=business)

        template_type = self.request.query_params.get('template_type')
        if template_type:
            queryset = queryset.filter(template_type=template_type)

        return queryset

    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)
