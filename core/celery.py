"""Celery application.

Django owns the request/response cycle and the database; anything slow or
outbound — WhatsApp, SMS, email, payment providers — runs here instead, so a
customer never waits on a third-party API and a provider outage cannot fail an
order.

Workers are started with:

    celery -A core worker -l info
    celery -A core beat -l info      # scheduled automation
"""

import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('core')

# All Celery settings live in Django settings under the CELERY_ prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Picks up tasks.py in every installed app.
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Smoke test: `celery -A core call core.celery.debug_task`."""
    print(f'Request: {self.request!r}')
