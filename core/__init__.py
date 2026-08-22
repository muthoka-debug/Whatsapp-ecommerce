"""Core project package.

Importing the Celery app here means `app.autodiscover_tasks()` has run by the
time Django starts, so `@shared_task` in any app is registered against it.
"""

from .celery import app as celery_app

__all__ = ('celery_app',)
