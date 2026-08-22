from django.urls import path

from . import views

urlpatterns = [
    path('', views.root_redirect, name='root'),
    path('login/', views.LoginPage.as_view(), name='login-page'),

    path('dashboard/', views.DashboardHome.as_view(), name='dashboard-home'),
    path('dashboard/products/', views.ProductsPage.as_view(), name='dashboard-products'),
    path('dashboard/orders/', views.OrdersPage.as_view(), name='dashboard-orders'),
    path('dashboard/customers/', views.CustomersPage.as_view(), name='dashboard-customers'),
    path('dashboard/deliveries/', views.DeliveriesPage.as_view(), name='dashboard-deliveries'),
    path('dashboard/automation/', views.AutomationPage.as_view(), name='dashboard-automation'),

    # Public storefront - architecture §10: yourplatform.com/shop/<slug>
    path('shop/<slug:slug>/', views.ShopPage.as_view(), name='shop'),
]
