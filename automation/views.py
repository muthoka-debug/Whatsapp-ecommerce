"""Automation API — the operator's window onto the worker.

The audit trail exists so that "the customer never got their confirmation" is a
question with an answer. This exposes it: what fired, what it did, what broke,
and what is still sitting in the queue.

Read-only apart from `retry`, which re-queues a run that failed or was stranded
by a broker outage. Nothing here creates events — those come from the business
code that emits them.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import IsBusinessManagerOrOwner, IsBusinessMember

from .events import Event
from .models import AutomationRun
from .serializers import AutomationRunSerializer

# A PENDING run older than this was almost certainly never picked up — the
# broker was down when it was emitted, or the worker died holding it. Anything
# younger is just in flight.
STALE_AFTER = timedelta(minutes=15)


class AutomationRunViewSet(viewsets.ReadOnlyModelViewSet):
    """Automation runs for the active business."""

    permission_classes = [IsAuthenticated, IsBusinessMember]
    serializer_class = AutomationRunSerializer
    queryset = AutomationRun.objects.none()  # OpenAPI model resolution

    def get_queryset(self):
        business = getattr(self.request, 'current_business', None)
        if not business:
            return AutomationRun.objects.none()

        queryset = AutomationRun.objects.filter(business=business)

        params = self.request.query_params

        status_filter = params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        event = params.get('event')
        if event:
            queryset = queryset.filter(event=event)

        subject_id = params.get('subject_id')
        if subject_id:
            # The support question is always about one order or one customer.
            queryset = queryset.filter(subject_id=subject_id)

        hours = params.get('hours')
        if hours:
            try:
                window = max(1, min(int(hours), 24 * 365))
            except (TypeError, ValueError):
                window = None
            if window:
                queryset = queryset.filter(
                    created_at__gte=timezone.now() - timedelta(hours=window)
                )

        if params.get('failed') == 'true':
            queryset = queryset.filter(status=AutomationRun.STATUS_FAILED)

        if params.get('stuck') == 'true':
            queryset = queryset.filter(
                status=AutomationRun.STATUS_PENDING,
                created_at__lt=timezone.now() - STALE_AFTER,
            )

        return queryset

    @action(detail=False, methods=['get'])
    def events(self, request):
        """The event vocabulary, for populating a filter.

        Served from Event.CHOICES rather than from the rows on hand, so the
        filter lists an event even when it has not fired yet — an empty result
        for ORDER_CANCELLED is an answer, and one an operator may be looking
        for.
        """
        return Response([
            {'value': value, 'label': label} for value, label in Event.CHOICES
        ])

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Health of the automation layer over a rolling window.

        `stuck` is the number worth alarming on: runs recorded but never
        executed means the broker or the worker is down, and every one of them
        is a message a customer did not get.
        """
        business = getattr(request, 'current_business', None)
        if not business:
            return Response({
                'window_hours': 24, 'total': 0, 'by_status': {}, 'by_event': [],
                'failed': 0, 'stuck': 0,
            })

        try:
            hours = max(1, min(int(request.query_params.get('hours', 24)), 24 * 30))
        except (TypeError, ValueError):
            hours = 24
        since = timezone.now() - timedelta(hours=hours)

        window = AutomationRun.objects.filter(business=business, created_at__gte=since)

        by_status = {
            row['status']: row['n']
            for row in window.values('status').annotate(n=Count('id'))
        }
        by_event = list(
            window.values('event')
            .annotate(
                n=Count('id'),
                failed=Count('id', filter=Q(status=AutomationRun.STATUS_FAILED)),
            )
            .order_by('-n')[:10]
        )

        # Stuck is deliberately measured over all time, not the window: a run
        # stranded three days ago is still stranded.
        stuck = AutomationRun.objects.filter(
            business=business,
            status=AutomationRun.STATUS_PENDING,
            created_at__lt=timezone.now() - STALE_AFTER,
        ).count()

        return Response({
            'window_hours': hours,
            'total': sum(by_status.values()),
            'by_status': by_status,
            'by_event': by_event,
            'failed': by_status.get(AutomationRun.STATUS_FAILED, 0),
            'stuck': stuck,
        })

    @action(
        detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsBusinessManagerOrOwner],
    )
    def retry(self, request, pk=None):
        """Re-queue a run that failed or never left the queue.

        Deliberately a manual, manager-level action rather than an automatic
        one. The handlers are not all safe to repeat from the customer's point
        of view — a retried ORDER_PAID sends a second receipt — so replaying is
        a judgement call about whether the message ever arrived.

        A run that already succeeded is refused: the idempotency key would not
        stop it, because the key guards emitting, not dispatching.
        """
        run = self.get_object()

        if run.status == AutomationRun.STATUS_SUCCESS:
            return Response(
                {'detail': 'This run already succeeded. Retrying it would repeat '
                           'every action it took, including any message sent.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        run.status = AutomationRun.STATUS_PENDING
        run.error = ''
        run.actions = []
        run.completed_at = None
        run.save(update_fields=['status', 'error', 'actions', 'completed_at', 'updated_at'])

        def _enqueue():
            from .tasks import run_automation
            run_automation.delay(str(run.pk))

        transaction.on_commit(_enqueue)

        return Response(self.get_serializer(run).data)
