from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CartViewSet, StorefrontViewSet, StorefrontSettingViewSet

router = DefaultRouter()
router.register(r'cart', CartViewSet, basename='cart')
router.register(r'settings', StorefrontSettingViewSet, basename='storefront-settings')

urlpatterns = [
    path('', include(router.urls)),
    # Public storefront endpoints (no auth required)
    path('public/<slug:slug>/', StorefrontViewSet.as_view({'get': 'info'}), name='storefront-info'),
    path('public/<slug:slug>/products/', StorefrontViewSet.as_view({'get': 'products'}), name='storefront-products'),
    path('public/<slug:slug>/categories/', StorefrontViewSet.as_view({'get': 'categories'}), name='storefront-categories'),
    path('public/<slug:slug>/settings/', StorefrontViewSet.as_view({'get': 'store_settings'}), name='storefront-public-settings'),
]