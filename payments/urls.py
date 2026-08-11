
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PaymentViewSet, MpesaCallbackView, MpesaC2BValidationView, MpesaC2BConfirmationView

router = DefaultRouter()
router.register(r'', PaymentViewSet, basename='payment')

urlpatterns = [
    path('', include(router.urls)),
    # M-Pesa Webhook endpoints (public)
    path('mpesa/callback/', MpesaCallbackView.as_view(), name='mpesa-callback'),
    path('mpesa/c2b/validation/', MpesaC2BValidationView.as_view(), name='mpesa-c2b-validation'),
    path('mpesa/c2b/confirmation/', MpesaC2BConfirmationView.as_view(), name='mpesa-c2b-confirmation'),
]