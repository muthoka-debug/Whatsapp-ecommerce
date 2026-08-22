"""Automation audit trail.

Every dispatched event gets a row. Two jobs:

1. **Observability.** When a customer says "I never got my confirmation", this
   is where you look — the event fired or it didn't, the action succeeded or it
   errored, with the traceback.

2. **Idempotency.** Celery redelivers a task if the worker dies mid-run, and
   Safaricom retries callbacks. `idempotency_key` is unique per business, so
   the second attempt at "remind cart X" loses the insert race and stops
   instead of messaging the customer twice.
"""

from django.db import models

from core.models import TenantAwareModel

from .events import Event


class AutomationRun(TenantAwareModel):
    """One dispatched event and what came of it."""

    STATUS_PENDING = 'PENDING'
    STATUS_SUCCESS = 'SUCCESS'
    STATUS_FAILED = 'FAILED'
    STATUS_SKIPPED = 'SKIPPED'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_SKIPPED, 'Skipped'),
    ]

    event = models.CharField(max_length=50, choices=Event.CHOICES)

    # What the event was about. Deliberately loose strings rather than a
    # GenericForeignKey: the subject may be soft-deleted or purged later and
    # the audit row should survive it.
    subject_type = models.CharField(max_length=50, blank=True)
    subject_id = models.CharField(max_length=100, blank=True)

    # Unique per business. Absent (blank) for events that may legitimately
    # repeat — a stock warning every morning is not a duplicate.
    idempotency_key = models.CharField(max_length=255, blank=True, db_index=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    payload = models.JSONField(default=dict, blank=True)
    actions = models.JSONField(default=list, blank=True)   # handler names that ran
    error = models.TextField(blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['business', 'event', '-created_at']),
            models.Index(fields=['business', 'status']),
            models.Index(fields=['subject_type', 'subject_id']),
        ]
        constraints = [
            # Partial unique: only rows that actually carry a key participate,
            # so the many blank-key rows never collide with each other.
            models.UniqueConstraint(
                fields=['business', 'idempotency_key'],
                condition=~models.Q(idempotency_key=''),
                name='automation_unique_idempotency_key_per_business',
            ),
        ]

    def __str__(self):
        return f'{self.event} [{self.status}] {self.subject_type}:{self.subject_id}'
