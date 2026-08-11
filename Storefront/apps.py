from django.apps import AppConfig

class StorefrontConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Storefront'
    def ready(self):
        import Storefront.signals