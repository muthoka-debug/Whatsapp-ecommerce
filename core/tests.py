"""Integration tests covering the tenant, ordering, payment and webhook paths.

These run against an isolated test database. Each test corresponds to a defect
that previously took the endpoint down, so they double as regression cover.
"""

import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.test import Client, TestCase, override_settings
from rest_framework.authtoken.models import Token

from accounts.models import User, UserBusinessMembership
from businesses.models import Business
from customers.models import Customer
from deliveries.models import Delivery
from orders.models import Order
from payments.models import MpesaTransaction, Payment
from products.models import Category, Product
from whatsapp_integration.models import WhatsAppSetting


class BaseTenantTestCase(TestCase):
    """Creates an owner, a business and one product."""

    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username='owner', email='owner@test.local',
            password='Str0ng!Passw0rd123', phone='0712345678',
        )
        self.business = Business.objects.create(
            name='Test Shop', phone='0712345678', email='shop@test.local',
            address='Nairobi', owner=self.owner,
        )
        UserBusinessMembership.objects.create(
            user=self.owner, business=self.business, role='OWNER', is_active=True,
        )
        self.token = Token.objects.create(user=self.owner)
        self.category = Category.objects.create(
            business=self.business, name='Drinks',
        )
        self.product = Product.objects.create(
            business=self.business, name='Soda', description='Cold',
            price=Decimal('100.00'), stock=10, low_stock_threshold=3,
            category=self.category, is_featured=True,
        )
        self.customer = Customer.objects.create(
            business=self.business, name='Jane', phone='0722000000',
        )

    @property
    def auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    @property
    def tenant(self):
        return dict(self.auth, HTTP_X_BUSINESS_ID=str(self.business.id))


class AuthenticationTests(TestCase):
    """rest_framework.authtoken was missing from INSTALLED_APPS, so the whole
    auth flow 500'd with 'no such table: authtoken_token'."""

    def test_register_and_login_issue_tokens(self):
        client = Client()
        response = client.post('/api/auth/register/', {
            'username': 'newuser', 'email': 'new@test.local',
            'phone': '0700111222',
            'password': 'Str0ng!Passw0rd123',
            'password_confirm': 'Str0ng!Passw0rd123',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn('token', response.json())

        response = client.post('/api/auth/login/', {
            'username': 'newuser', 'password': 'Str0ng!Passw0rd123',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('token', response.json())


class BusinessContextTests(BaseTenantTestCase):
    """BusinessMiddleware was never installed, so request.current_business did
    not exist and every business-scoped view raised AttributeError."""

    def test_scoped_endpoints_resolve_the_tenant(self):
        for url in (
            '/api/v1/products/',
            '/api/v1/products/categories/',
            '/api/v1/orders/',
            '/api/v1/customers/',
            '/api/v1/analytics/dashboard/summary/',
            '/api/v1/notifications/',
            '/api/v1/whatsapp/conversations/',
        ):
            with self.subTest(url=url):
                response = self.client.get(url, **self.tenant)
                self.assertEqual(response.status_code, 200, f'{url}: {response.content[:200]}')

    def test_missing_tenant_header_is_denied_not_crashed(self):
        response = self.client.get('/api/v1/products/', **self.auth)
        self.assertEqual(response.status_code, 403)


class ProductTests(BaseTenantTestCase):
    """The serializer referenced a dozen fields the model never defined."""

    def test_product_serializer_renders_every_declared_field(self):
        response = self.client.get('/api/v1/products/', **self.tenant)
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        row = payload[0] if isinstance(payload, list) else payload['results'][0]
        for field in ('compare_price', 'cost_price', 'low_stock_threshold',
                      'barcode', 'is_featured', 'weight', 'dimensions', 'tags',
                      'in_stock', 'is_low_stock'):
            self.assertIn(field, row)

    def test_sku_is_generated_when_blank(self):
        product = Product.objects.create(
            business=self.business, name='Water', price=Decimal('50.00'),
        )
        self.assertTrue(product.sku)

    def test_reduce_stock_is_atomic_and_refuses_oversell(self):
        self.product.reduce_stock(4)
        self.assertEqual(self.product.stock, 6)

        with self.assertRaises(ValueError):
            self.product.reduce_stock(999)

        self.product.increase_stock(4)
        self.assertEqual(self.product.stock, 10)

    def test_update_stock_endpoint(self):
        response = self.client.post(
            f'/api/v1/products/{self.product.id}/update_stock/',
            {'quantity': 5, 'action': 'add'},
            content_type='application/json', **self.tenant,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['new_stock'], 15)

    def test_delete_is_soft(self):
        self.product.delete()
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_deleted)


class OrderTests(BaseTenantTestCase):
    def test_create_order_folds_tax_shipping_and_discount_into_total(self):
        response = self.client.post('/api/v1/orders/', {
            'customer': self.customer.id, 'source': 'MANUAL',
            'tax': '16.00', 'shipping_fee': '50.00', 'discount': '10.00',
            'items': [{'product': self.product.id, 'quantity': 2}],
        }, content_type='application/json', **self.tenant)
        self.assertEqual(response.status_code, 201, response.content)

        order = Order.objects.get(business=self.business)
        # 2 x 100 line items + 16 tax + 50 shipping - 10 discount
        self.assertEqual(order.total_amount, Decimal('256.00'))
        self.assertEqual(order.subtotal, Decimal('200.00'))
        self.assertTrue(order.order_number)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)

    def test_order_number_is_unique_across_orders_created_together(self):
        numbers = {
            Order.objects.create(
                business=self.business, customer=self.customer,
            ).order_number
            for _ in range(25)
        }
        self.assertEqual(len(numbers), 25)

    def test_update_status_writes_real_timestamps(self):
        order = Order.objects.create(business=self.business, customer=self.customer)
        response = self.client.post(
            f'/api/v1/orders/{order.id}/update_status/', {'status': 'CONFIRMED'},
            content_type='application/json', **self.tenant,
        )
        self.assertEqual(response.status_code, 200, response.content)

        order.refresh_from_db()
        self.assertIsInstance(order.confirmed_at, datetime)
        self.assertEqual(order.confirmed_by, self.owner)

    def test_cancelling_an_order_returns_stock(self):
        self.client.post('/api/v1/orders/', {
            'customer': self.customer.id,
            'items': [{'product': self.product.id, 'quantity': 3}],
        }, content_type='application/json', **self.tenant)
        order = Order.objects.get(business=self.business)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)

        self.client.post(
            f'/api/v1/orders/{order.id}/update_status/', {'status': 'CANCELLED'},
            content_type='application/json', **self.tenant,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)


class PaymentTests(BaseTenantTestCase):
    def setUp(self):
        super().setUp()
        self.order = Order.objects.create(
            business=self.business, customer=self.customer,
            total_amount=Decimal('256.00'),
        )

    def test_complete_payment_writes_a_datetime_not_a_field_object(self):
        payment = Payment.objects.create(
            business=self.business, order=self.order,
            amount=Decimal('256.00'), method='CASH',
        )
        payment.complete_payment('TXN-1')
        payment.refresh_from_db()

        self.assertIsInstance(payment.completed_at, datetime)
        self.assertEqual(payment.status, 'COMPLETED')

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'PAID')
        self.assertIsInstance(self.order.paid_at, datetime)

    def test_non_cashier_gets_403_not_500(self):
        member = User.objects.create_user(
            username='viewer', email='v@test.local', password='Str0ng!Passw0rd123',
        )
        UserBusinessMembership.objects.create(
            user=member, business=self.business, role='STAFF', is_active=True,
        )
        # STAFF may create payments; a role outside the allowed set must 403.
        UserBusinessMembership.objects.filter(user=member).update(role='CASHIER')
        token = Token.objects.create(user=member)
        response = self.client.get('/api/v1/payments/', **{
            'HTTP_AUTHORIZATION': f'Token {token.key}',
            'HTTP_X_BUSINESS_ID': str(self.business.id),
        })
        self.assertEqual(response.status_code, 200, response.content)


@override_settings(MPESA_CALLBACK_SECRET='cb-secret', MPESA_CALLBACK_ALLOWED_IPS=[])
class MpesaCallbackTests(BaseTenantTestCase):
    """The callback endpoints accepted anything, so anyone who guessed a
    CheckoutRequestID could mark an order PAID."""

    def setUp(self):
        super().setUp()
        self.order = Order.objects.create(
            business=self.business, customer=self.customer,
            total_amount=Decimal('256.00'),
        )
        self.payment = Payment.objects.create(
            business=self.business, order=self.order,
            amount=Decimal('256.00'), method='MPESA', status='PROCESSING',
        )
        self.transaction = MpesaTransaction.objects.create(
            business=self.business, payment=self.payment,
            transaction_type='STK_PUSH', checkout_request_id='ws_CO_1',
            phone_number='254712345678', amount=Decimal('256.00'),
            account_reference=self.order.order_number, status='PROCESSING',
        )
        self.body = {'Body': {'stkCallback': {
            'CheckoutRequestID': 'ws_CO_1', 'ResultCode': 0, 'ResultDesc': 'OK',
            'CallbackMetadata': {'Item': [
                {'Name': 'MpesaReceiptNumber', 'Value': 'QWE123'},
                {'Name': 'Amount', 'Value': 256},
            ]},
        }}}

    def test_callback_without_secret_is_rejected(self):
        response = self.client.post(
            '/api/v1/payments/mpesa/callback/', self.body,
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status, 'PROCESSING')

    def test_callback_with_secret_completes_the_payment(self):
        response = self.client.post(
            '/api/v1/payments/mpesa/callback/?token=cb-secret', self.body,
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)

        self.transaction.refresh_from_db()
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.transaction.status, 'COMPLETED')
        self.assertEqual(self.payment.status, 'COMPLETED')
        self.assertEqual(self.order.status, 'PAID')

    def test_duplicate_callback_is_idempotent(self):
        url = '/api/v1/payments/mpesa/callback/?token=cb-secret'
        self.client.post(url, self.body, content_type='application/json')
        first_completed_at = MpesaTransaction.objects.get(
            pk=self.transaction.pk
        ).completed_at

        response = self.client.post(url, self.body, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            MpesaTransaction.objects.get(pk=self.transaction.pk).completed_at,
            first_completed_at,
        )


@override_settings(WHATSAPP_APP_SECRET='app-secret', DEBUG=False)
class WhatsAppWebhookTests(BaseTenantTestCase):
    """The webhook was unauthenticated and created customers/orders for anyone."""

    def setUp(self):
        super().setUp()
        self.setting = WhatsAppSetting.objects.create(
            business=self.business, phone_number_id='PN1',
            business_account_id='BA1', access_token='secret-token',
            display_phone_number='254712345678', display_name='Test Shop',
            webhook_verify_token='verify-123',
        )

    @staticmethod
    def _sign(body):
        return 'sha256=' + hmac.new(
            b'app-secret', body.encode(), hashlib.sha256
        ).hexdigest()

    def test_unsigned_request_is_rejected(self):
        response = self.client.post(
            '/api/v1/whatsapp/webhook/', json.dumps({'entry': []}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_bad_signature_is_rejected(self):
        response = self.client.post(
            '/api/v1/whatsapp/webhook/', json.dumps({'entry': []}),
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256='sha256=deadbeef',
        )
        self.assertEqual(response.status_code, 403)

    def test_valid_signature_is_accepted(self):
        body = json.dumps({'entry': []})
        response = self.client.post(
            '/api/v1/whatsapp/webhook/', body, content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=self._sign(body),
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_inbound_message_is_stored_with_a_real_timestamp(self):
        body = json.dumps({'entry': [{'changes': [{'value': {
            'metadata': {'display_phone_number': '254712345678'},
            'messages': [{
                'from': '254722000000', 'id': 'wamid.TEST1',
                'timestamp': '1700000000', 'type': 'text',
                'text': {'body': 'hello'},
            }],
        }}]}]})
        response = self.client.post(
            '/api/v1/whatsapp/webhook/', body, content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=self._sign(body),
        )
        self.assertEqual(response.status_code, 200, response.content)

        from whatsapp_integration.models import WhatsAppMessage
        message = WhatsAppMessage.objects.get(message_id='wamid.TEST1')
        self.assertEqual(message.content, 'hello')
        self.assertIsInstance(message.sent_at, datetime)

    def test_verify_handshake_requires_a_known_token(self):
        response = self.client.get(
            '/api/v1/whatsapp/webhook/',
            {'hub.verify_token': 'wrong', 'hub.challenge': '123'},
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.get(
            '/api/v1/whatsapp/webhook/',
            {'hub.verify_token': 'verify-123', 'hub.challenge': '123'},
        )
        self.assertEqual(response.status_code, 200)


class EncryptedCredentialTests(BaseTenantTestCase):
    def test_access_token_is_ciphertext_at_rest(self):
        setting = WhatsAppSetting.objects.create(
            business=self.business, phone_number_id='PN1',
            business_account_id='BA1', access_token='super-secret',
            display_phone_number='254799000000', display_name='Shop',
            webhook_verify_token='verify-abc',
        )
        with connection.cursor() as cursor:
            # SQLite stores UUID pks as 32 hex chars with no dashes.
            cursor.execute(
                'SELECT access_token FROM whatsapp_integration_whatsappsetting '
                'WHERE id = %s', [setting.id.hex],
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row, 'setting row not found')
        stored = row[0]

        if settings.FIELD_ENCRYPTION_KEY:
            self.assertTrue(stored.startswith('enc$v1$'))
            self.assertNotIn('super-secret', stored)
        self.assertEqual(
            WhatsAppSetting.objects.get(pk=setting.pk).access_token,
            'super-secret',
        )


class NotificationServiceTests(BaseTenantTestCase):
    """NotificationService read Business fields that did not exist and called
    ``recipient.user`` on objects that were already Users."""

    def setUp(self):
        super().setUp()
        self.order = Order.objects.create(
            business=self.business, customer=self.customer,
            total_amount=Decimal('100.00'), created_by=self.owner,
        )

    def test_order_confirmation_creates_an_in_app_notification(self):
        from notifications.models import Notification
        from notifications.services import NotificationService

        notification = NotificationService(self.business).send_order_confirmation(
            self.order
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification.recipient, self.owner)
        self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())

    def test_recipient_falls_back_to_the_owner(self):
        from notifications.services import NotificationService

        self.order.created_by = None
        self.order.save(update_fields=['created_by'])

        notification = NotificationService(self.business).send_order_confirmation(
            self.order
        )
        self.assertEqual(notification.recipient, self.owner)

    def test_customer_whatsapp_mirror_uses_the_customer_phone(self):
        """The stored notification's recipient FK only accepts a User, so the
        customer-facing copy must not be written onto it."""
        from unittest.mock import patch

        from notifications.services import NotificationService

        WhatsAppSetting.objects.create(
            business=self.business, phone_number_id='PN1',
            business_account_id='BA1', access_token='tok',
            display_phone_number='254712345678', display_name='Shop',
            webhook_verify_token='verify-xyz',
        )
        self.business.whatsapp_auto_order_confirmation = True
        self.business.save(update_fields=['whatsapp_auto_order_confirmation'])

        with patch(
            'whatsapp_integration.client.WhatsAppClient.send_text',
            return_value='wamid.OUT1',
        ) as send_text:
            notification = NotificationService(
                self.business
            ).send_order_confirmation(self.order)

        self.assertIsNotNone(notification)
        send_text.assert_called_once()
        # Delivered to the customer, not to the staff recipient.
        self.assertEqual(send_text.call_args[0][0], self.customer.phone)

    def test_order_status_update_notification(self):
        from notifications.services import NotificationService

        notification = NotificationService(self.business).send_order_status_update(
            self.order, 'PENDING', 'CONFIRMED'
        )
        self.assertIsNotNone(notification)
        self.assertIn('CONFIRMED', notification.message)


class TenantIsolationTests(BaseTenantTestCase):
    def setUp(self):
        super().setUp()
        self.other_user = User.objects.create_user(
            username='other', email='other@test.local',
            password='Str0ng!Passw0rd123',
        )
        self.other_business = Business.objects.create(
            name='Other Shop', phone='0700000000', email='other@test.local',
            address='Mombasa', owner=self.other_user,
        )
        UserBusinessMembership.objects.create(
            user=self.other_user, business=self.other_business,
            role='OWNER', is_active=True,
        )
        other_token = Token.objects.create(user=self.other_user)
        self.other_headers = {
            'HTTP_AUTHORIZATION': f'Token {other_token.key}',
            'HTTP_X_BUSINESS_ID': str(self.other_business.id),
        }

    def test_other_tenant_cannot_read_our_order(self):
        order = Order.objects.create(business=self.business, customer=self.customer)
        response = self.client.get(f'/api/v1/orders/{order.id}/', **self.other_headers)
        self.assertEqual(response.status_code, 404)

    def test_owner_of_another_business_cannot_add_members_to_ours(self):
        """The role check read the X-Business-ID header rather than the business
        named in the URL, so an owner of any business passed the owner check."""
        response = self.client.post(
            f'/api/v1/businesses/{self.business.id}/add_member/',
            {'user_id': self.other_user.id, 'role': 'OWNER'},
            content_type='application/json', **self.other_headers,
        )
        self.assertIn(response.status_code, (403, 404), response.content)
        self.assertFalse(
            UserBusinessMembership.objects.filter(
                user=self.other_user, business=self.business
            ).exists()
        )


class StorefrontTests(BaseTenantTestCase):
    """The public storefront filtered on Product.is_deleted / is_featured and
    called Product.reduce_stock(), none of which existed."""

    def test_public_product_and_category_listings(self):
        anon = Client()
        response = anon.get(f'/api/v1/storefront/public/{self.business.slug}/products/')
        self.assertEqual(response.status_code, 200, response.content)

        response = anon.get(f'/api/v1/storefront/public/{self.business.slug}/categories/')
        self.assertEqual(response.status_code, 200, response.content)

    def test_anonymous_cart_and_checkout(self):
        anon = Client()
        headers = {'HTTP_X_BUSINESS_ID': str(self.business.id)}

        response = anon.get('/api/v1/storefront/cart/', **headers)
        self.assertEqual(response.status_code, 200, response.content)

        response = anon.post(
            '/api/v1/storefront/cart/add_item/',
            {'product_id': self.product.id, 'quantity': 2},
            content_type='application/json', **headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(str(response.json()['subtotal'])), Decimal('200.00'))

        response = anon.post('/api/v1/storefront/cart/checkout/', {
            'customer_name': 'Walk In', 'customer_phone': '0733111222',
            'delivery_address': 'CBD', 'delivery_phone': '0733111222',
            'payment_method': 'CASH',
        }, content_type='application/json', **headers)
        self.assertEqual(response.status_code, 201, response.content)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)


class PaymentSerializerTests(BaseTenantTestCase):
    """`class Meta:6` is valid Python (a one-line class body), so model/fields
    landed on the serializer instead of Meta. An empty queryset never builds the
    child fields, so only non-empty payment responses failed."""

    def test_payment_list_renders_with_rows(self):
        order = Order.objects.create(
            business=self.business, customer=self.customer,
            total_amount=Decimal('100.00'),
        )
        Payment.objects.create(
            business=self.business, order=order,
            amount=Decimal('100.00'), method='CASH', status='COMPLETED',
            transaction_id='TXN-SER-1',
        )

        response = self.client.get('/api/v1/payments/', **self.tenant)
        self.assertEqual(response.status_code, 200, response.content)

        payload = response.json()
        rows = payload if isinstance(payload, list) else payload['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['transaction_id'], 'TXN-SER-1')
        self.assertIn('order_details', rows[0])


class DeliveryTests(BaseTenantTestCase):
    """§11 Delivery management."""

    def setUp(self):
        super().setUp()
        self.order = Order.objects.create(
            business=self.business, customer=self.customer,
            total_amount=Decimal('200.00'),
        )

    def _create_delivery(self, **overrides):
        payload = {
            'order': self.order.id,
            'address': 'Kilimani, Nairobi',
            'phone': '0722000000',
            'method': 'RIDER',
            'fee': '150.00',
        }
        payload.update(overrides)
        return self.client.post(
            '/api/v1/deliveries/', payload,
            content_type='application/json', **self.tenant,
        )

    def test_create_delivery_for_an_order(self):
        response = self._create_delivery()
        self.assertEqual(response.status_code, 201, response.content)

        delivery = Delivery.objects.get(order=self.order)
        self.assertEqual(delivery.status, 'PENDING')
        self.assertEqual(delivery.recipient_name, self.customer.name)

    def test_delivery_fee_flows_into_the_order_total(self):
        self._create_delivery(fee='150.00')

        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_fee, Decimal('150.00'))
        # No line items, so the total is just the shipping fee.
        self.assertEqual(self.order.total_amount, Decimal('150.00'))

    def test_one_delivery_per_order(self):
        self._create_delivery()
        response = self._create_delivery()
        self.assertEqual(response.status_code, 400, response.content)

    def test_cannot_create_delivery_for_another_business_order(self):
        other_user = User.objects.create_user(
            username='other2', email='o2@test.local', password='Str0ng!Passw0rd123',
        )
        other_business = Business.objects.create(
            name='Other Co', phone='0700000001', email='o2@test.local',
            address='Kisumu', owner=other_user,
        )
        other_customer = Customer.objects.create(
            business=other_business, name='Bob', phone='0733000000',
        )
        foreign_order = Order.objects.create(
            business=other_business, customer=other_customer,
        )

        response = self._create_delivery(order=foreign_order.id)
        self.assertEqual(response.status_code, 400, response.content)

    def test_full_status_lifecycle_drives_the_order(self):
        self._create_delivery()
        delivery = Delivery.objects.get(order=self.order)

        response = self.client.post(
            f'/api/v1/deliveries/{delivery.id}/assign/',
            {'assigned_to': self.owner.id},
            content_type='application/json', **self.tenant,
        )
        self.assertEqual(response.status_code, 200, response.content)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, 'ASSIGNED')
        self.assertEqual(delivery.assigned_to, self.owner)

        self.client.post(
            f'/api/v1/deliveries/{delivery.id}/mark_picked_up/', **self.tenant
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, 'PICKED_UP')

        self.client.post(
            f'/api/v1/deliveries/{delivery.id}/mark_in_transit/', **self.tenant
        )
        delivery.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(delivery.status, 'IN_TRANSIT')
        self.assertEqual(self.order.status, 'OUT_FOR_DELIVERY')

        self.client.post(
            f'/api/v1/deliveries/{delivery.id}/mark_delivered/', **self.tenant
        )
        delivery.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(delivery.status, 'DELIVERED')
        self.assertEqual(self.order.status, 'DELIVERED')
        self.assertIsInstance(self.order.delivered_at, datetime)

    def test_terminal_status_rejects_further_transitions(self):
        self._create_delivery()
        delivery = Delivery.objects.get(order=self.order)
        delivery.mark_delivered()

        response = self.client.post(
            f'/api/v1/deliveries/{delivery.id}/mark_in_transit/', **self.tenant
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_mark_failed_requires_a_reason(self):
        self._create_delivery()
        delivery = Delivery.objects.get(order=self.order)

        response = self.client.post(
            f'/api/v1/deliveries/{delivery.id}/mark_failed/', {},
            content_type='application/json', **self.tenant,
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            f'/api/v1/deliveries/{delivery.id}/mark_failed/',
            {'reason': 'Customer unreachable'},
            content_type='application/json', **self.tenant,
        )
        self.assertEqual(response.status_code, 200, response.content)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, 'FAILED')
        self.assertEqual(delivery.failure_reason, 'Customer unreachable')

    def test_cannot_assign_a_non_member(self):
        self._create_delivery()
        delivery = Delivery.objects.get(order=self.order)
        outsider = User.objects.create_user(
            username='outsider', email='out@test.local',
            password='Str0ng!Passw0rd123',
        )

        response = self.client.post(
            f'/api/v1/deliveries/{delivery.id}/assign/',
            {'assigned_to': outsider.id},
            content_type='application/json', **self.tenant,
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_cashier_cannot_change_delivery_status(self):
        self._create_delivery()
        delivery = Delivery.objects.get(order=self.order)

        cashier = User.objects.create_user(
            username='cashier', email='c@test.local', password='Str0ng!Passw0rd123',
        )
        UserBusinessMembership.objects.create(
            user=cashier, business=self.business, role='CASHIER', is_active=True,
        )
        headers = {
            'HTTP_AUTHORIZATION': f'Token {Token.objects.create(user=cashier).key}',
            'HTTP_X_BUSINESS_ID': str(self.business.id),
        }

        response = self.client.post(
            f'/api/v1/deliveries/{delivery.id}/mark_delivered/', **headers
        )
        self.assertEqual(response.status_code, 403, response.content)

        # ...but may still read the board.
        response = self.client.get('/api/v1/deliveries/', **headers)
        self.assertEqual(response.status_code, 200, response.content)

    def test_summary_counts_by_status(self):
        self._create_delivery()
        response = self.client.get('/api/v1/deliveries/summary/', **self.tenant)
        self.assertEqual(response.status_code, 200, response.content)

        body = response.json()
        self.assertEqual(body['by_status']['PENDING'], 1)
        self.assertEqual(body['active'], 1)
        self.assertEqual(body['total'], 1)

    def test_order_create_accepts_a_delivery_block(self):
        response = self.client.post('/api/v1/orders/', {
            'customer': self.customer.id,
            'tax': '10.00',
            'items': [{'product': self.product.id, 'quantity': 1}],
            'delivery': {
                'address': 'Westlands',
                'phone': '0722111333',
                'method': 'COURIER',
                'fee': '200.00',
            },
        }, content_type='application/json', **self.tenant)
        self.assertEqual(response.status_code, 201, response.content)

        order = Order.objects.exclude(pk=self.order.pk).get()
        delivery = Delivery.objects.get(order=order)
        self.assertEqual(delivery.method, 'COURIER')
        self.assertEqual(delivery.fee, Decimal('200.00'))

        order.refresh_from_db()
        # 1 x 100 line item + 10 tax + 200 delivery
        self.assertEqual(order.total_amount, Decimal('310.00'))

    def test_order_payload_embeds_its_delivery(self):
        self._create_delivery()
        response = self.client.get(
            f'/api/v1/orders/{self.order.id}/', **self.tenant
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()['delivery']['address'], 'Kilimani, Nairobi'
        )

    def test_storefront_checkout_creates_a_delivery(self):
        anon = Client()
        headers = {'HTTP_X_BUSINESS_ID': str(self.business.id)}
        anon.post(
            '/api/v1/storefront/cart/add_item/',
            {'product_id': self.product.id, 'quantity': 1},
            content_type='application/json', **headers,
        )
        response = anon.post('/api/v1/storefront/cart/checkout/', {
            'customer_name': 'Walk In', 'customer_phone': '0733111222',
            'delivery_address': 'Ngong Road', 'delivery_phone': '0733111222',
            'payment_method': 'CASH',
        }, content_type='application/json', **headers)
        self.assertEqual(response.status_code, 201, response.content)

        order = Order.objects.get(source='STOREFRONT')
        delivery = Delivery.objects.get(order=order)
        self.assertEqual(delivery.address, 'Ngong Road')
        self.assertEqual(delivery.status, 'PENDING')

    def test_delivery_of_another_tenant_is_not_visible(self):
        self._create_delivery()
        delivery = Delivery.objects.get(order=self.order)

        other_user = User.objects.create_user(
            username='other3', email='o3@test.local', password='Str0ng!Passw0rd123',
        )
        other_business = Business.objects.create(
            name='Other Three', phone='0700000003', email='o3@test.local',
            address='Nakuru', owner=other_user,
        )
        UserBusinessMembership.objects.create(
            user=other_user, business=other_business, role='OWNER', is_active=True,
        )
        headers = {
            'HTTP_AUTHORIZATION': f'Token {Token.objects.create(user=other_user).key}',
            'HTTP_X_BUSINESS_ID': str(other_business.id),
        }

        response = self.client.get(
            f'/api/v1/deliveries/{delivery.id}/', **headers
        )
        self.assertEqual(response.status_code, 404)


class SerializerIntegrityTests(TestCase):
    """Force every serializer in the project to build its fields.

    This is the check that would have caught `class Meta:6` in
    payments/serializers.py: that is valid Python, so model/fields silently
    attached to the serializer instead of Meta, and DRF only failed once a
    response actually contained a row. Any serializer referencing a field the
    model does not have fails here instead of in production.
    """

    def test_every_serializer_builds_its_fields(self):
        import importlib
        import inspect

        from django.apps import apps as django_apps
        from rest_framework import serializers as drf_serializers

        local_apps = [
            config.name for config in django_apps.get_app_configs()
            if not config.name.startswith(
                ('django.', 'rest_framework', 'corsheaders', 'drf_yasg',
                 'django_filters')
            )
        ]

        failures = []
        checked = 0

        for app_name in local_apps:
            try:
                module = importlib.import_module(f'{app_name}.serializers')
            except ModuleNotFoundError:
                continue

            for name, obj in vars(module).items():
                if not (inspect.isclass(obj)
                        and issubclass(obj, drf_serializers.BaseSerializer)):
                    continue
                if obj.__module__ != module.__name__:
                    continue  # re-exported from another module

                try:
                    built = obj().fields
                except Exception as exc:
                    failures.append(f'{app_name}.{name}: {type(exc).__name__}: {exc}')
                    continue

                meta = getattr(obj, 'Meta', None)
                declared = getattr(meta, 'fields', None) if meta else None
                if isinstance(declared, (list, tuple)):
                    missing = set(declared) - set(built)
                    if missing:
                        failures.append(
                            f'{app_name}.{name}: declared but not built: '
                            f'{sorted(missing)}'
                        )
                checked += 1

        self.assertGreater(checked, 30, 'serializer discovery found too few classes')
        self.assertEqual(failures, [], 'Broken serializers:\n' + '\n'.join(failures))

    def test_every_url_resolves_to_a_view(self):
        from django.urls import URLPattern, URLResolver, get_resolver

        def walk(patterns, prefix=''):
            for entry in patterns:
                if isinstance(entry, URLResolver):
                    yield from walk(entry.url_patterns, prefix + str(entry.pattern))
                elif isinstance(entry, URLPattern):
                    yield prefix + str(entry.pattern), entry.callback

        routes = list(walk(get_resolver().url_patterns))
        self.assertGreater(len(routes), 100)
        for route, callback in routes:
            self.assertIsNotNone(callback, f'{route} has no view')


class PaginationTests(BaseTenantTestCase):
    def test_list_endpoints_are_paginated(self):
        for index in range(30):
            Product.objects.create(
                business=self.business, name=f'Item {index}',
                price=Decimal('10.00'), stock=1,
            )

        response = self.client.get('/api/v1/products/', **self.tenant)
        self.assertEqual(response.status_code, 200, response.content)

        body = response.json()
        self.assertIn('count', body)
        self.assertIn('results', body)
        # 30 created here + the 1 from setUp, capped at PAGE_SIZE.
        self.assertEqual(body['count'], 31)
        self.assertEqual(len(body['results']), settings.REST_FRAMEWORK['PAGE_SIZE'])
        self.assertIsNotNone(body['next'])


class ThrottlingTests(TestCase):
    """Exercises the real configured rates.

    override_settings cannot be used here: DRF's SimpleRateThrottle binds
    THROTTLE_RATES as a class attribute at import time, so a swapped-in
    REST_FRAMEWORK dict is never seen by the throttle.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # throttle history is cache-backed

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()

    @staticmethod
    def _auth_limit():
        rate = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['auth']
        return int(rate.split('/')[0])

    def test_login_endpoint_is_rate_limited(self):
        client = Client()
        payload = {'username': 'nobody', 'password': 'wrong-password'}

        statuses = [
            client.post('/api/auth/login/', payload,
                        content_type='application/json').status_code
            for _ in range(self._auth_limit() + 2)
        ]
        self.assertIn(429, statuses, f'login was never throttled: {statuses}')
        # Everything before the cap should be a normal auth failure, not a 429.
        self.assertEqual(statuses[0], 401)

    def test_webhooks_are_never_throttled(self):
        """Meta and Safaricom burst legitimately; a 429 would drop real events."""
        client = Client()
        statuses = []
        for _ in range(8):
            response = client.post(
                '/api/v1/payments/mpesa/callback/', {},
                content_type='application/json',
            )
            statuses.append(response.status_code)

        self.assertNotIn(429, statuses, f'webhook got throttled: {statuses}')


class ApiDocsTests(BaseTenantTestCase):
    """drf-yasg was a dependency that was never wired into the URLconf."""

    def test_schema_and_swagger_ui_are_served(self):
        import json as json_module

        response = self.client.get('/api/schema.json?format=json', **self.auth)
        self.assertEqual(response.status_code, 200, response.content[:300])

        schema = json_module.loads(response.content)
        self.assertIn('paths', schema)
        # Spot-check that the real routes made it into the schema.
        self.assertTrue(
            any(path.startswith('/api/v1/deliveries/') for path in schema['paths']),
            'deliveries endpoints missing from the schema',
        )

        response = self.client.get('/api/docs/', **self.auth)
        self.assertEqual(response.status_code, 200)

    def test_schema_requires_authentication(self):
        response = Client().get('/api/schema.json')
        self.assertIn(response.status_code, (401, 403), response.content[:200])


class FrontendPageTests(BaseTenantTestCase):
    """Phase 3 page shells. They render without data and without auth; the API
    behind them is what enforces access."""

    def test_dashboard_pages_render(self):
        for url in ('/dashboard/', '/dashboard/products/', '/dashboard/orders/',
                    '/dashboard/customers/', '/dashboard/deliveries/'):
            with self.subTest(url=url):
                response = Client().get(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertContains(response, 'js/shell.js')

    def test_login_page_renders(self):
        response = Client().get('/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'login-form')

    def test_root_redirects_to_dashboard(self):
        response = Client().get('/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/dashboard/')

    def test_shop_page_renders_for_a_real_slug(self):
        response = Client().get(f'/shop/{self.business.slug}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.business.name)

    def test_unknown_shop_slug_404s(self):
        response = Client().get('/shop/no-such-shop/')
        self.assertEqual(response.status_code, 404)

    def test_frontend_routes_do_not_shadow_the_api(self):
        """The catch-all frontend include is registered last."""
        response = self.client.get('/api/v1/products/', **self.tenant)
        self.assertEqual(response.status_code, 200, response.content[:200])


class StorefrontInfoTests(BaseTenantTestCase):
    """The public info endpoint maps a slug to the business id the cart needs
    in X-Business-ID. Without it an anonymous visitor cannot use the cart."""

    def test_info_returns_business_and_settings(self):
        response = Client().get(f'/api/v1/storefront/public/{self.business.slug}/')
        self.assertEqual(response.status_code, 200, response.content)

        body = response.json()
        self.assertEqual(body['business']['id'], self.business.id)
        self.assertEqual(body['business']['slug'], self.business.slug)
        self.assertEqual(body['business']['currency'], self.business.currency)
        self.assertIn('settings', body)

    def test_info_needs_no_authentication(self):
        response = Client().get(f'/api/v1/storefront/public/{self.business.slug}/')
        self.assertEqual(response.status_code, 200)

    def test_hidden_storefront_is_not_served(self):
        from Storefront.models import StorefrontSetting

        setting, _ = StorefrontSetting.objects.get_or_create(business=self.business)
        setting.is_visible = False
        setting.save(update_fields=['is_visible'])

        response = Client().get(f'/api/v1/storefront/public/{self.business.slug}/')
        self.assertEqual(response.status_code, 404)

    def test_anonymous_can_complete_a_purchase_using_only_public_data(self):
        """End-to-end of what shop.js does: info -> products -> cart -> checkout."""
        anon = Client()

        info = anon.get(f'/api/v1/storefront/public/{self.business.slug}/').json()
        headers = {'HTTP_X_BUSINESS_ID': str(info['business']['id'])}

        listing = anon.get(f'/api/v1/storefront/public/{self.business.slug}/products/')
        self.assertEqual(listing.status_code, 200)
        product_id = listing.json()['results'][0]['id']

        added = anon.post(
            '/api/v1/storefront/cart/add_item/',
            {'product_id': product_id, 'quantity': 2},
            content_type='application/json', **headers,
        )
        self.assertEqual(added.status_code, 200, added.content)
        self.assertEqual(added.json()['total_items'], 2)

        done = anon.post('/api/v1/storefront/cart/checkout/', {
            'customer_name': 'Walk In', 'customer_phone': '0733111222',
            'delivery_address': 'Kilimani', 'delivery_phone': '0733111222',
            'payment_method': 'CASH',
        }, content_type='application/json', **headers)
        self.assertEqual(done.status_code, 201, done.content)
        self.assertTrue(done.json()['order']['order_number'])


class FrontendApiContractTests(BaseTenantTestCase):
    """Pins the response keys the JavaScript reads.

    The browser code is not exercised by this suite, so a renamed API field
    would otherwise surface as a silently blank dashboard tile rather than a
    failing test. Each assertion below mirrors a specific read in static/js.
    """

    def setUp(self):
        super().setUp()
        self.order = Order.objects.create(
            business=self.business, customer=self.customer,
            status='PAID', source='STOREFRONT', total_amount=Decimal('250.00'),
        )
        Payment.objects.create(
            business=self.business, order=self.order,
            amount=Decimal('250.00'), method='MPESA', status='PENDING',
        )

    def test_dashboard_summary_shape(self):
        """dashboard.js loadSummary()"""
        body = self.client.get(
            '/api/v1/analytics/dashboard/summary/', **self.tenant
        ).json()

        for key in ('revenue', 'orders', 'revenue_growth', 'order_growth'):
            self.assertIn(key, body['today'])
        for key in ('orders', 'payments'):
            self.assertIn(key, body['pending'])
        self.assertIn('total_products', body['inventory'])
        self.assertIn('low_stock', body['inventory'])
        self.assertIn('total', body['customers'])
        self.assertIsInstance(body['recent_orders'], list)

        # renderRecentOrders() reads these off each row.
        row = body['recent_orders'][0]
        for key in ('id', 'order_number', 'status', 'status_display',
                    'placed_at', 'total_amount', 'customer_details'):
            self.assertIn(key, row)
        self.assertIn('name', row['customer_details'])

    def test_revenue_chart_shape(self):
        """dashboard.js loadRevenue()"""
        body = self.client.get(
            '/api/v1/analytics/dashboard/revenue_chart/?period=30', **self.tenant
        ).json()

        self.assertIn('total_revenue', body)
        self.assertIn('total_orders', body)
        self.assertIsInstance(body['data'], list)
        for key in ('date', 'revenue', 'orders'):
            self.assertIn(key, body['data'][0])

    def test_sales_by_source_shape(self):
        """dashboard.js loadSources()"""
        payload = self.client.get(
            '/api/v1/analytics/dashboard/sales_by_source/', **self.tenant
        ).json()

        entries = payload if isinstance(payload, list) else payload['results']
        self.assertTrue(entries, 'expected at least one channel row')
        for key in ('source', 'total_orders', 'total_revenue'):
            self.assertIn(key, entries[0])

    def test_product_row_shape(self):
        """products.js renderRow() and dashboard.js loadLowStock()"""
        payload = self.client.get('/api/v1/products/', **self.tenant).json()
        row = payload['results'][0]

        for key in ('id', 'name', 'sku', 'price', 'stock', 'is_active',
                    'is_featured', 'is_low_stock', 'low_stock_threshold',
                    'category_details'):
            self.assertIn(key, row)

    def test_order_detail_shape(self):
        """orders.js renderDetail()"""
        body = self.client.get(f'/api/v1/orders/{self.order.id}/', **self.tenant).json()

        for key in ('order_number', 'status', 'status_display', 'source_display',
                    'placed_at', 'subtotal', 'tax', 'shipping_fee', 'discount',
                    'total_amount', 'items', 'customer_details', 'delivery'):
            self.assertIn(key, body)

    def test_delivery_summary_and_row_shape(self):
        """deliveries.js loadSummary() and loadDeliveries()"""
        Delivery.objects.create(
            business=self.business, order=self.order,
            address='Kilimani', phone='0722000000', fee=Decimal('150.00'),
        )

        summary = self.client.get('/api/v1/deliveries/summary/', **self.tenant).json()
        for key in ('by_status', 'active', 'total', 'fees_collected'):
            self.assertIn(key, summary)
        self.assertIn('PENDING', summary['by_status'])
        self.assertIn('IN_TRANSIT', summary['by_status'])

        row = self.client.get('/api/v1/deliveries/', **self.tenant).json()['results'][0]
        for key in ('id', 'order_number', 'recipient_name', 'address', 'method',
                    'method_display', 'status', 'status_display', 'fee'):
            self.assertIn(key, row)

    def test_cart_shape(self):
        """shop.js renderCart()"""
        anon = Client()
        headers = {'HTTP_X_BUSINESS_ID': str(self.business.id)}
        cart = anon.post(
            '/api/v1/storefront/cart/add_item/',
            {'product_id': self.product.id, 'quantity': 1},
            content_type='application/json', **headers,
        ).json()

        for key in ('items', 'total_items', 'subtotal'):
            self.assertIn(key, cart)
        for key in ('id', 'quantity', 'price', 'total', 'product_details'):
            self.assertIn(key, cart['items'][0])

    def test_login_response_shape(self):
        """login.js reads token, user and the caller's memberships."""
        body = Client().post('/api/auth/login/', {
            'username': 'owner', 'password': 'Str0ng!Passw0rd123',
        }, content_type='application/json').json()

        self.assertIn('token', body)
        self.assertIn('user', body)
        self.assertIsInstance(body['businesses'], list)
        self.assertIn('business', body['businesses'][0])

    def test_business_list_shape(self):
        """shell.js builds the switcher from these."""
        row = self.client.get('/api/v1/businesses/', **self.auth).json()['results'][0]
        for key in ('id', 'name', 'slug', 'currency'):
            self.assertIn(key, row)


class StaticAssetTests(TestCase):
    """The frontend is inert without these files; a rename would otherwise only
    surface as a blank page in the browser."""

    def test_frontend_assets_are_findable(self):
        from django.contrib.staticfiles import finders

        for asset in ('css/app.css', 'js/api.js', 'js/charts.js', 'js/shell.js',
                      'js/modal.js', 'js/login.js', 'js/dashboard.js',
                      'js/products.js', 'js/orders.js', 'js/customers.js',
                      'js/deliveries.js', 'js/shop.js'):
            with self.subTest(asset=asset):
                self.assertIsNotNone(finders.find(asset), f'missing static file: {asset}')


class HealthCheckTests(TestCase):
    def test_health_endpoint(self):
        response = Client().get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'healthy')
