"""Page shells for the dashboard and the public storefront.

These views render HTML only — no data. Every value on the page is fetched by
the browser from the REST API, which keeps one authorization path (token +
X-Business-ID) instead of a second, server-rendered one that could drift from it.

Auth is therefore enforced client-side for *navigation* (no token in
localStorage -> bounce to /login/) and server-side for *data* by DRF. A shell
served to a signed-out visitor exposes nothing: it is an empty page.
"""

from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView

from businesses.models import Business


class DashboardPageView(TemplateView):
    """Base for authenticated dashboard pages."""

    nav = ''  # which nav item to mark current

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav=self.nav, **kwargs)


class LoginPage(TemplateView):
    template_name = 'dashboard/login.html'


class DashboardHome(DashboardPageView):
    template_name = 'dashboard/index.html'
    nav = 'dashboard'


class ProductsPage(DashboardPageView):
    template_name = 'dashboard/products.html'
    nav = 'products'


class OrdersPage(DashboardPageView):
    template_name = 'dashboard/orders.html'
    nav = 'orders'


class CustomersPage(DashboardPageView):
    template_name = 'dashboard/customers.html'
    nav = 'customers'


class DeliveriesPage(DashboardPageView):
    template_name = 'dashboard/deliveries.html'
    nav = 'deliveries'


class ShopPage(TemplateView):
    """Public storefront at /shop/<slug>/.

    The slug is resolved here so an unknown shop 404s as a page rather than
    rendering an empty shell that only fails once JavaScript runs.
    """

    template_name = 'shop/index.html'

    def get_context_data(self, slug, **kwargs):
        business = get_object_or_404(Business, slug=slug)
        return super().get_context_data(
            slug=slug, business_name=business.name, **kwargs
        )


def root_redirect(request):
    """Send / to the dashboard; the client bounces on to login if signed out."""
    return redirect('dashboard-home')
