from django.apps import AppConfig


class AutomationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'automation'
    verbose_name = 'Automation'

    def ready(self):
        # Importing the handlers module runs the @handles decorators, which is
        # what populates the dispatcher's registry. Without this the events
        # fire, find no handlers, and every run is silently SKIPPED.
        from . import handlers  # noqa: F401
