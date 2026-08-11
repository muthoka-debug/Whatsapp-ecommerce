"""URL configuration for core project.

Route layout:
    /admin/                 Django admin
    /api/auth/              registration, login, logout, profile, tokens
    /api/v1/<resource>/     business-scoped REST resources
    /api/docs/              Swagger UI  (/api/redoc/, /api/schema.json)
    /health/                health check for monitoring
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.authtoken import views as token_views

urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),

    # Authentication
    path('api/auth/', include('accounts.urls')),
    path('api/auth/token/', token_views.obtain_auth_token, name='api-token'),

    # API Version 1
    path('api/v1/businesses/', include('businesses.urls')),
    path('api/v1/products/', include('products.urls')),
    path('api/v1/customers/', include('customers.urls')),
    path('api/v1/orders/', include('orders.urls')),
    path('api/v1/payments/', include('payments.urls')),
    path('api/v1/deliveries/', include('deliveries.urls')),
    path('api/v1/storefront/', include('Storefront.urls')),
    path('api/v1/whatsapp/', include('whatsapp_integration.urls')),
    path('api/v1/analytics/', include('analytics.urls')),
    path('api/v1/notifications/', include('notifications.urls')),

    # API documentation
    path('api/schema.json', SpectacularAPIView.as_view(), name='api-schema'),
    path(
        'api/docs/',
        SpectacularSwaggerView.as_view(url_name='api-schema'),
        name='api-docs',
    ),
    path(
        'api/redoc/',
        SpectacularRedocView.as_view(url_name='api-schema'),
        name='api-redoc',
    ),

    # Health check
    path('health/', include('core.health_urls')),

    # Frontend pages (dashboard + public storefront). Last so it never shadows
    # an API route.
    path('', include('frontend.urls')),
]
