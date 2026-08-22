"""Read models for the automation audit trail.

Everything here is read-only. A run is a record of something that already
happened; the only write the API offers is a retry, which re-queues the
existing row rather than accepting a new one from the client.
"""

from rest_framework import serializers

from .models import AutomationRun


class AutomationRunSerializer(serializers.ModelSerializer):
    event_display = serializers.CharField(source='get_event_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    duration_ms = serializers.SerializerMethodField()

    class Meta:
        model = AutomationRun
        fields = [
            'id', 'event', 'event_display', 'status', 'status_display',
            'subject_type', 'subject_id', 'idempotency_key',
            'payload', 'actions', 'error',
            'duration_ms', 'created_at', 'completed_at',
        ]
        read_only_fields = fields

    def get_duration_ms(self, run):
        """How long the worker took, or None while the run is still pending.

        Queue latency is included on purpose: the number the operator cares
        about is "how long after the event did the customer hear from us",
        not the handler's own runtime.
        """
        if not run.completed_at:
            return None
        return int((run.completed_at - run.created_at).total_seconds() * 1000)
