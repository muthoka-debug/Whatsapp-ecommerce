"""Event emission and dispatch.

Business code calls `emit()` and moves on. Everything else — deciding what
should happen, talking to WhatsApp, retrying — happens later, in a worker.

Two rules hold the design together:

**Emit never raises.** An automation problem must not fail the order it was
announcing. Callers get no error and no return value they need to check.

**Emit fires after commit.** The row is enqueued through
`transaction.on_commit`, so a worker can never pick up an order that the
database has not committed yet — the classic Celery/Django race, and one that
looks like a random "order does not exist" in production. It also means a
rolled-back transaction sends nothing, which is the behaviour you want.
"""

import logging
from collections import defaultdict

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AutomationRun

logger = logging.getLogger(__name__)

# event name -> [handler, ...]
HANDLERS = defaultdict(list)


def handles(*events):
    """Register a handler for one or more events.

        @handles(Event.ORDER_PAID)
        def send_receipt(run, business, payload): ...

    Handlers run in registration order and are isolated: one raising does not
    stop the others, it just marks the run FAILED with the traceback.
    """
    def decorator(func):
        for event in events:
            HANDLERS[event].append(func)
        return func
    return decorator


def emit(event, business, *, subject=None, idempotency_key='', delay=None, **payload):
    """Announce that something happened. Returns the AutomationRun, or None if
    it was suppressed (duplicate key, no business, or an internal error).

    `subject`   any model instance the event is about; recorded for the audit trail.
    `delay`     timedelta to wait before running — for "two days after delivery".
    `payload`   JSON-serialisable extras the handlers need.
    """
    if business is None:
        logger.warning('automation.emit(%s) called without a business; ignored', event)
        return None

    subject_type = subject.__class__.__name__ if subject is not None else ''
    subject_id = str(subject.pk) if subject is not None else ''

    try:
        # The savepoint is load-bearing, not defensive style. emit() is called
        # from inside business transactions, and on PostgreSQL an IntegrityError
        # aborts the whole transaction — catching it without a savepoint would
        # leave the caller unable to run another query ("current transaction is
        # aborted"), turning a harmless duplicate event into a failed checkout.
        with transaction.atomic():
            run = AutomationRun.objects.create(
                business=business,
                event=event,
                subject_type=subject_type,
                subject_id=subject_id,
                idempotency_key=idempotency_key,
                payload=payload,
            )
    except IntegrityError:
        # Lost the race on the unique key — someone already emitted this.
        logger.info('automation: %s for %s already emitted (%s)',
                    event, subject_id, idempotency_key)
        return None
    except Exception:
        logger.exception('automation: could not record %s', event)
        return None

    def _enqueue():
        from .tasks import run_automation
        try:
            if delay is not None:
                run_automation.apply_async(args=[str(run.pk)], countdown=delay.total_seconds())
            else:
                run_automation.delay(str(run.pk))
        except Exception:
            # Broker down. The row survives with status PENDING so the work is
            # recoverable rather than silently lost.
            logger.exception('automation: could not enqueue run %s (%s)', run.pk, event)

    transaction.on_commit(_enqueue)
    return run


def dispatch(run_id):
    """Execute the handlers for a recorded event. Called by the Celery task.

    Runs every handler even if an earlier one failed — a WhatsApp outage should
    not also cost you the stock adjustment — and records the first error.
    """
    try:
        run = AutomationRun.objects.select_related('business').get(pk=run_id)
    except AutomationRun.DoesNotExist:
        logger.warning('automation: run %s no longer exists', run_id)
        return

    if run.status != AutomationRun.STATUS_PENDING:
        # Redelivered after we already finished. Nothing to do.
        logger.info('automation: run %s already %s', run_id, run.status)
        return

    handlers = HANDLERS.get(run.event, [])
    if not handlers:
        run.status = AutomationRun.STATUS_SKIPPED
        run.completed_at = timezone.now()
        run.save(update_fields=['status', 'completed_at', 'updated_at'])
        return

    ran, errors = [], []
    for handler in handlers:
        name = f'{handler.__module__}.{handler.__name__}'
        try:
            handler(run, run.business, run.payload)
            ran.append(name)
        except Exception as exc:
            logger.exception('automation: handler %s failed for %s', name, run.event)
            errors.append(f'{name}: {exc}')

    run.actions = ran
    run.error = '\n'.join(errors)
    run.status = AutomationRun.STATUS_FAILED if errors else AutomationRun.STATUS_SUCCESS
    run.completed_at = timezone.now()
    run.save(update_fields=['actions', 'error', 'status', 'completed_at', 'updated_at'])
