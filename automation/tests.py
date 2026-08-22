"""Tests for the automation layer: emit, dispatch, handlers and sweeps.

The suite runs with CELERY_TASK_ALWAYS_EAGER and a local-memory cache, so it
needs neither a worker nor Redis. That is deliberate — see the note in
core.settings — but it means the queue hop is simulated, not exercised. What is
exercised here is everything either side of it: the row that gets written
before the task is queued, and the work the task does when it arrives.

Two things these tests keep honest, because both fail silently in production:

* **emit() fires on commit, not on call.** Django's TestCase wraps each test in
  a transaction that never commits, so `captureOnCommitCallbacks(execute=True)`
  is how a test says "now the request finished". A test that forgets it sees a
  PENDING row and no side effects — which is exactly what a worker would see if
  the transaction had rolled back.

* **emit() never raises.** Every failure mode below (no business, duplicate
  key, dead broker) must leave the caller's order or payment untouched.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import User, UserBusinessMembership
from analytics.models import AIInsight
from businesses.models import Business
from customers.models import Customer
from orders.models import Order
from products.models import Category, Product
from Storefront.models import Cart, CartItem

from .events import Event
from .models import AutomationRun
from .services import HANDLERS, dispatch, emit
from .tasks import (
    sweep_abandoned_carts,
    sweep_inactive_customers,
    sweep_low_stock,
)


class AutomationTestCase(TestCase):
    """A business with one product, one customer, and no notifications sent.

    NotificationService is patched out by default: these tests are about which
    handler ran and what it decided, not about message rendering, which
    core.tests covers. Tests that care about the message opt back in.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username='auto-owner', email='auto@test.local',
            password='Str0ng!Passw0rd123', phone='0712345678',
        )
        self.business = Business.objects.create(
            name='Automation Shop', phone='0712345678',
            email='auto-shop@test.local', address='Nairobi', owner=self.owner,
        )
        UserBusinessMembership.objects.create(
            user=self.owner, business=self.business, role='OWNER', is_active=True,
        )
        self.category = Category.objects.create(business=self.business, name='Drinks')
        self.product = Product.objects.create(
            business=self.business, name='Soda', description='Cold',
            price=Decimal('100.00'), stock=10, low_stock_threshold=3,
            category=self.category,
        )
        self.customer = Customer.objects.create(
            business=self.business, name='Jane', phone='0722000000',
        )

        patcher = patch('automation.handlers.NotificationService')
        self.notifications = patcher.start().return_value
        self.addCleanup(patcher.stop)

    # -- helpers ----------------------------------------------------------

    def other_business(self):
        """A second tenant, for the isolation checks."""
        owner = User.objects.create_user(
            username='rival-owner', email='rival@test.local',
            password='Str0ng!Passw0rd123', phone='0799000000',
        )
        return Business.objects.create(
            name='Rival Shop', phone='0799000000', email='rival-shop@test.local',
            address='Mombasa', owner=owner,
        )

    def make_order(self, *, status='PENDING', customer=None, business=None):
        return Order.objects.create(
            business=business or self.business,
            customer=customer or self.customer,
            status=status,
            total_amount=Decimal('100.00'),
            created_by=self.owner,
        )

    def make_cart(self, *, business=None, customer='default', items=True, idle_hours=None):
        cart = Cart.objects.create(
            business=business or self.business,
            customer=self.customer if customer == 'default' else customer,
            is_active=True,
        )
        if items:
            CartItem.objects.create(
                business=cart.business, cart=cart, product=self.product,
                quantity=2, price=self.product.price,
            )
        if idle_hours is not None:
            # updated_at is auto_now, so it cannot be set through save().
            self.backdate(Cart, cart.pk, 'updated_at', hours=idle_hours)
            cart.refresh_from_db()
        return cart

    @staticmethod
    def backdate(model, pk, field, **delta):
        """Push an auto_now/auto_now_add timestamp into the past."""
        model.objects.filter(pk=pk).update(**{field: timezone.now() - timedelta(**delta)})

    @staticmethod
    def register(event, *handlers):
        """Temporarily register handlers for an event.

        HANDLERS is module-level state shared by the whole process, so anything
        added here has to come back off or it leaks into later tests.
        """
        HANDLERS[event].extend(handlers)


# ---------------------------------------------------------------------------
# emit()
# ---------------------------------------------------------------------------

class EmitTests(AutomationTestCase):

    def test_run_is_recorded_but_not_dispatched_until_commit(self):
        """The row exists immediately; the work waits for the commit.

        This is the ordering that stops a worker from loading an order the
        database has not written yet.
        """
        with patch('automation.tasks.run_automation.delay') as delay:
            run = emit(Event.ORDER_CREATED, self.business, subject=self.make_order())
            self.assertIsNotNone(run)
            self.assertEqual(run.status, AutomationRun.STATUS_PENDING)
            delay.assert_not_called()

    def test_commit_enqueues_and_the_handler_runs(self):
        order = self.make_order()

        with self.captureOnCommitCallbacks(execute=True):
            run = emit(Event.ORDER_CREATED, self.business, subject=order)

        run.refresh_from_db()
        self.assertEqual(run.status, AutomationRun.STATUS_SUCCESS)
        self.assertEqual(run.error, '')
        self.assertIn(
            'automation.handlers.notify_order_created', run.actions,
        )
        self.notifications.send_order_confirmation.assert_called_once()
        self.assertEqual(
            self.notifications.send_order_confirmation.call_args[0][0].pk, order.pk,
        )

    def test_subject_is_recorded_for_the_audit_trail(self):
        order = self.make_order()
        run = emit(Event.ORDER_PAID, self.business, subject=order, old_status='PENDING')

        self.assertEqual(run.subject_type, 'Order')
        self.assertEqual(run.subject_id, str(order.pk))
        self.assertEqual(run.payload, {'old_status': 'PENDING'})

    def test_emit_without_a_business_is_ignored_not_raised(self):
        self.assertIsNone(emit(Event.ORDER_CREATED, None, subject=self.make_order()))
        self.assertEqual(AutomationRun.objects.count(), 0)

    def test_duplicate_idempotency_key_is_suppressed(self):
        order = self.make_order()
        key = f'order-status:{order.pk}:PAID'

        first = emit(Event.ORDER_PAID, self.business, subject=order, idempotency_key=key)
        second = emit(Event.ORDER_PAID, self.business, subject=order, idempotency_key=key)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(AutomationRun.objects.filter(idempotency_key=key).count(), 1)

    def test_blank_keys_never_collide(self):
        """A stock warning every morning is not a duplicate; the unique
        constraint is partial so unkeyed events stay independent."""
        for _ in range(3):
            self.assertIsNotNone(emit(Event.PRODUCT_STOCK_LOW, self.business))
        self.assertEqual(AutomationRun.objects.count(), 3)

    def test_the_same_key_in_another_business_is_allowed(self):
        rival = self.other_business()
        self.assertIsNotNone(emit(Event.ORDER_PAID, self.business, idempotency_key='k'))
        self.assertIsNotNone(emit(Event.ORDER_PAID, rival, idempotency_key='k'))
        self.assertEqual(AutomationRun.objects.filter(idempotency_key='k').count(), 2)

    def test_a_duplicate_does_not_poison_the_callers_transaction(self):
        """The savepoint in emit() earning its keep.

        On PostgreSQL an IntegrityError aborts the surrounding transaction, so
        without the inner atomic() a duplicate event would take the checkout
        down with it — every subsequent query failing with "current transaction
        is aborted".
        """
        emit(Event.ORDER_PAID, self.business, idempotency_key='dupe')

        with transaction.atomic():
            self.assertIsNone(
                emit(Event.ORDER_PAID, self.business, idempotency_key='dupe')
            )
            # The caller must still be able to work.
            self.assertEqual(Product.objects.filter(business=self.business).count(), 1)
            Product.objects.filter(pk=self.product.pk).update(stock=7)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)

    def test_a_dead_broker_leaves_the_run_recoverable(self):
        """Redis down must not fail the request — and must not lose the work."""
        with patch(
            'automation.tasks.run_automation.delay',
            side_effect=OSError('Connection refused'),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                run = emit(Event.ORDER_CREATED, self.business, subject=self.make_order())

        run.refresh_from_db()
        self.assertEqual(run.status, AutomationRun.STATUS_PENDING)
        self.notifications.send_order_confirmation.assert_not_called()

    def test_a_rolled_back_transaction_sends_nothing(self):
        with patch('automation.tasks.run_automation.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        emit(Event.ORDER_CREATED, self.business, subject=self.make_order())
                        raise RuntimeError('checkout failed after the emit')
                except RuntimeError:
                    pass

        delay.assert_not_called()
        self.assertEqual(AutomationRun.objects.count(), 0)

    def test_delay_schedules_the_run_instead_of_queueing_it_now(self):
        with patch('automation.tasks.run_automation.apply_async') as apply_async:
            with self.captureOnCommitCallbacks(execute=True):
                run = emit(
                    Event.FEEDBACK_DUE, self.business, subject=self.make_order(),
                    delay=timedelta(hours=48),
                )

        apply_async.assert_called_once()
        self.assertEqual(apply_async.call_args.kwargs['args'], [str(run.pk)])
        self.assertEqual(apply_async.call_args.kwargs['countdown'], 48 * 3600)


# ---------------------------------------------------------------------------
# dispatch()
# ---------------------------------------------------------------------------

class DispatchTests(AutomationTestCase):

    def make_run(self, event, **kwargs):
        return AutomationRun.objects.create(business=self.business, event=event, **kwargs)

    def test_an_event_nobody_handles_is_skipped_not_failed(self):
        """Producers are allowed to run ahead of consumers."""
        run = self.make_run('SOMETHING_NEW')
        dispatch(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, AutomationRun.STATUS_SKIPPED)
        self.assertIsNotNone(run.completed_at)
        self.assertEqual(run.error, '')

    def test_one_failing_handler_does_not_stop_the_others(self):
        calls = []

        def boom(run, business, payload):
            raise ValueError('whatsapp is down')

        def still_runs(run, business, payload):
            calls.append('still_runs')

        self.register('MULTI_HANDLER', boom, still_runs)
        self.addCleanup(HANDLERS.pop, 'MULTI_HANDLER', None)

        run = self.make_run('MULTI_HANDLER')
        dispatch(run.pk)

        run.refresh_from_db()
        self.assertEqual(calls, ['still_runs'])
        self.assertEqual(run.status, AutomationRun.STATUS_FAILED)
        self.assertIn('whatsapp is down', run.error)
        # The one that worked is still credited.
        self.assertEqual(len(run.actions), 1)
        self.assertTrue(run.actions[0].endswith('still_runs'))

    def test_a_redelivered_run_does_not_act_twice(self):
        """acks_late means the broker may hand the same task over again after a
        worker dies. The status check is what makes that safe."""
        calls = []
        self.register('ONCE_ONLY', lambda run, business, payload: calls.append(1))
        self.addCleanup(HANDLERS.pop, 'ONCE_ONLY', None)

        run = self.make_run('ONCE_ONLY')
        dispatch(run.pk)
        dispatch(run.pk)

        self.assertEqual(calls, [1])

    def test_a_run_deleted_before_the_worker_arrives_is_ignored(self):
        run = self.make_run(Event.ORDER_CREATED)
        pk = run.pk
        run.delete()

        dispatch(pk)  # must not raise

    def test_a_handler_cannot_reach_another_tenants_row(self):
        """A task argument is just an ID. _load() scopes every read to the
        event's business, so a replayed or forged ID finds nothing."""
        rival = self.other_business()
        rival_customer = Customer.objects.create(
            business=rival, name='Rival Customer', phone='0733000000',
        )
        rival_order = self.make_order(business=rival, customer=rival_customer)

        run = self.make_run(
            Event.ORDER_CREATED, subject_type='Order', subject_id=str(rival_order.pk),
        )
        dispatch(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, AutomationRun.STATUS_SUCCESS)  # handled, found nothing
        self.notifications.send_order_confirmation.assert_not_called()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

class HandlerTests(AutomationTestCase):
    """Handlers re-read their subject, so the interesting cases are the ones
    where the world changed between the event and the worker picking it up."""

    def dispatch_now(self, event, subject, **payload):
        run = AutomationRun.objects.create(
            business=self.business, event=event,
            subject_type=subject.__class__.__name__, subject_id=str(subject.pk),
            payload=payload,
        )
        dispatch(run.pk)
        run.refresh_from_db()
        return run

    def test_status_change_notifies_with_both_statuses(self):
        order = self.make_order(status='PAID')
        self.dispatch_now(
            Event.ORDER_PAID, order, old_status='PENDING', new_status='PAID',
        )

        self.notifications.send_order_status_update.assert_called_once()
        args = self.notifications.send_order_status_update.call_args[0]
        self.assertEqual(args[1:], ('PENDING', 'PAID'))

    def test_delivery_schedules_a_single_feedback_request(self):
        order = self.make_order(status='DELIVERED')

        with patch('automation.tasks.run_automation.apply_async'):
            with self.captureOnCommitCallbacks(execute=True):
                self.dispatch_now(Event.ORDER_DELIVERED, order)
                # A second delivery event (redelivered, or re-marked) must not
                # queue a second request.
                self.dispatch_now(Event.ORDER_DELIVERED, order)

        self.assertEqual(
            AutomationRun.objects.filter(event=Event.FEEDBACK_DUE).count(), 1,
        )

    @override_settings(DELIVERY_FEEDBACK_AFTER_HOURS=6)
    def test_the_feedback_delay_is_configurable(self):
        order = self.make_order(status='DELIVERED')

        with patch('automation.tasks.run_automation.apply_async') as apply_async:
            with self.captureOnCommitCallbacks(execute=True):
                self.dispatch_now(Event.ORDER_DELIVERED, order)

        self.assertEqual(apply_async.call_args.kwargs['countdown'], 6 * 3600)

    def test_feedback_is_skipped_if_the_delivery_was_reversed(self):
        """Two days is long enough for a delivery to come back."""
        order = self.make_order(status='CANCELLED')
        self.dispatch_now(Event.FEEDBACK_DUE, order)

        self.notifications.send_feedback_request.assert_not_called()

    def test_feedback_is_sent_when_the_order_is_still_delivered(self):
        order = self.make_order(status='DELIVERED')
        self.dispatch_now(Event.FEEDBACK_DUE, order)

        self.notifications.send_feedback_request.assert_called_once()

    def test_low_stock_alert_is_dropped_after_a_restock(self):
        self.product.stock = 1
        self.product.save(update_fields=['stock'])
        run = AutomationRun.objects.create(
            business=self.business, event=Event.PRODUCT_STOCK_LOW,
            subject_type='Product', subject_id=str(self.product.pk),
            payload={'stock': 1, 'threshold': 3},
        )

        Product.objects.filter(pk=self.product.pk).update(stock=50)
        dispatch(run.pk)

        self.notifications.send_low_stock_alert.assert_not_called()

    def test_low_stock_alert_is_sent_while_stock_is_still_low(self):
        Product.objects.filter(pk=self.product.pk).update(stock=1)
        self.dispatch_now(Event.PRODUCT_STOCK_LOW, self.product)

        self.notifications.send_low_stock_alert.assert_called_once()

    def test_abandoned_cart_reminder_is_dropped_once_the_cart_is_checked_out(self):
        cart = self.make_cart()
        run = AutomationRun.objects.create(
            business=self.business, event=Event.CART_ABANDONED,
            subject_type='Cart', subject_id=str(cart.pk),
        )

        cart.clear()  # checkout empties the cart and deactivates it
        dispatch(run.pk)

        self.notifications.send_abandoned_cart_reminder.assert_not_called()

    def test_abandoned_cart_reminder_is_sent_for_a_live_cart(self):
        cart = self.make_cart()
        self.dispatch_now(Event.CART_ABANDONED, cart)

        self.notifications.send_abandoned_cart_reminder.assert_called_once()

    def test_inactive_customer_is_flagged_without_being_messaged(self):
        """Unsolicited outbound to a customer who went quiet is how a WhatsApp
        sender gets blocked, so this handler writes an insight and stops."""
        self.dispatch_now(Event.CUSTOMER_INACTIVE, self.customer, inactive_days=90)

        insight = AIInsight.objects.get(business=self.business)
        self.assertEqual(insight.insight_type, 'CUSTOMER_SEGMENT')
        self.assertIn('90', insight.description)
        self.assertEqual(insight.data['customer_id'], str(self.customer.id))
        self.assertTrue(insight.is_actionable)

        for method in (
            'send_order_confirmation', 'send_abandoned_cart_reminder',
            'send_order_status_update',
        ):
            getattr(self.notifications, method).assert_not_called()


# ---------------------------------------------------------------------------
# Scheduled sweeps (Celery Beat)
# ---------------------------------------------------------------------------

class AbandonedCartSweepTests(AutomationTestCase):

    def run_sweep(self):
        with patch('automation.tasks.run_automation.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                emitted = sweep_abandoned_carts()
        return emitted, delay

    def test_an_idle_cart_with_items_is_emitted_once(self):
        cart = self.make_cart(idle_hours=48)

        emitted, _ = self.run_sweep()
        self.assertEqual(emitted, 1)

        run = AutomationRun.objects.get(event=Event.CART_ABANDONED)
        self.assertEqual(run.subject_id, str(cart.pk))
        self.assertEqual(run.idempotency_key, f'cart-abandoned:{cart.pk}')

        # A cart that stays abandoned is reminded once, not once per hour.
        self.assertEqual(self.run_sweep()[0], 0)
        self.assertEqual(AutomationRun.objects.filter(event=Event.CART_ABANDONED).count(), 1)

    def test_a_fresh_cart_is_left_alone(self):
        self.make_cart(idle_hours=1)
        self.assertEqual(self.run_sweep()[0], 0)

    @override_settings(ABANDONED_CART_AFTER_HOURS=2)
    def test_the_idle_window_is_configurable(self):
        self.make_cart(idle_hours=3)
        self.assertEqual(self.run_sweep()[0], 1)

    def test_an_empty_cart_is_not_worth_a_reminder(self):
        self.make_cart(items=False, idle_hours=48)
        self.assertEqual(self.run_sweep()[0], 0)

    def test_an_anonymous_cart_has_nobody_to_remind(self):
        self.make_cart(customer=None, idle_hours=48)
        self.assertEqual(self.run_sweep()[0], 0)

    def test_a_checked_out_cart_is_excluded(self):
        cart = self.make_cart(idle_hours=48)
        Cart.objects.filter(pk=cart.pk).update(is_active=False)
        self.assertEqual(self.run_sweep()[0], 0)

    def test_another_tenants_cart_is_scoped_to_that_tenant(self):
        """The sweep walks businesses one at a time; a cart must be emitted
        against its own business, never ours."""
        rival = self.other_business()
        rival_customer = Customer.objects.create(
            business=rival, name='Rival Customer', phone='0733000000',
        )
        rival_product = Product.objects.create(
            business=rival, name='Water', description='Still',
            price=Decimal('50.00'), stock=10, low_stock_threshold=2,
        )
        rival_cart = Cart.objects.create(
            business=rival, customer=rival_customer, is_active=True,
        )
        CartItem.objects.create(
            business=rival, cart=rival_cart, product=rival_product,
            quantity=1, price=rival_product.price,
        )
        self.backdate(Cart, rival_cart.pk, 'updated_at', hours=48)

        self.assertEqual(self.run_sweep()[0], 1)
        run = AutomationRun.objects.get(event=Event.CART_ABANDONED)
        self.assertEqual(run.business_id, rival.id)
        self.assertEqual(AutomationRun.objects.filter(business=self.business).count(), 0)


class LowStockSweepTests(AutomationTestCase):

    def run_sweep(self):
        with patch('automation.tasks.run_automation.delay'):
            with self.captureOnCommitCallbacks(execute=True):
                return sweep_low_stock()

    def test_stock_at_or_below_the_threshold_is_reported_as_low(self):
        Product.objects.filter(pk=self.product.pk).update(stock=3)

        self.assertEqual(self.run_sweep(), 1)
        run = AutomationRun.objects.get()
        self.assertEqual(run.event, Event.PRODUCT_STOCK_LOW)
        self.assertEqual(run.payload, {'stock': 3, 'threshold': 3})

    def test_zero_stock_is_reported_as_out_of_stock(self):
        Product.objects.filter(pk=self.product.pk).update(stock=0)

        self.assertEqual(self.run_sweep(), 1)
        self.assertEqual(AutomationRun.objects.get().event, Event.PRODUCT_OUT_OF_STOCK)

    def test_healthy_stock_is_silent(self):
        self.assertEqual(self.run_sweep(), 0)

    def test_at_most_one_alert_per_product_per_day(self):
        """The key carries the date: the sweep and the sell-out path in
        Product.reduce_stock converge on one alert, and tomorrow's low stock is
        worth mentioning again."""
        Product.objects.filter(pk=self.product.pk).update(stock=1)

        self.assertEqual(self.run_sweep(), 1)
        self.assertEqual(self.run_sweep(), 0)

        today = timezone.now().date().isoformat()
        self.assertEqual(
            AutomationRun.objects.get().idempotency_key,
            f'stock:{self.product.pk}:{today}',
        )

    def test_an_inactive_product_is_not_reported(self):
        Product.objects.filter(pk=self.product.pk).update(stock=0, is_active=False)
        self.assertEqual(self.run_sweep(), 0)


class InactiveCustomerSweepTests(AutomationTestCase):

    def run_sweep(self):
        with patch('automation.tasks.run_automation.delay'):
            with self.captureOnCommitCallbacks(execute=True):
                return sweep_inactive_customers()

    def order_aged(self, days, customer=None):
        order = self.make_order(customer=customer)
        self.backdate(Order, order.pk, 'placed_at', days=days)
        return order

    def test_a_customer_past_the_window_is_flagged(self):
        self.order_aged(90)

        self.assertEqual(self.run_sweep(), 1)
        run = AutomationRun.objects.get(event=Event.CUSTOMER_INACTIVE)
        self.assertEqual(run.subject_id, str(self.customer.pk))
        self.assertGreaterEqual(run.payload['inactive_days'], 89)

    def test_a_recent_order_keeps_a_customer_active(self):
        self.order_aged(3)
        self.assertEqual(self.run_sweep(), 0)

    def test_the_most_recent_order_is_the_one_that_counts(self):
        """Annotating on Max() matters: an old order must not flag a customer
        who bought again last week."""
        self.order_aged(200)
        self.order_aged(2)
        self.assertEqual(self.run_sweep(), 0)

    def test_a_customer_who_never_ordered_is_a_lead_not_a_lapse(self):
        Customer.objects.create(business=self.business, name='Browser', phone='0744000000')
        self.assertEqual(self.run_sweep(), 0)

    @override_settings(CUSTOMER_INACTIVE_AFTER_DAYS=10)
    def test_the_inactivity_window_is_configurable(self):
        self.order_aged(20)
        self.assertEqual(self.run_sweep(), 1)

    def test_a_weekly_sweep_does_not_re_flag_the_same_day(self):
        self.order_aged(90)
        self.assertEqual(self.run_sweep(), 1)
        self.assertEqual(self.run_sweep(), 0)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class AutomationApiTests(AutomationTestCase):
    """The operator's window onto the trail. Tenant scoping is the load-bearing
    part: a run names an order and an error, both of which are another
    business's private data."""

    def setUp(self):
        super().setUp()
        from rest_framework.authtoken.models import Token

        self.client = Client()
        self.token = Token.objects.create(user=self.owner)
        self.headers = {
            'HTTP_AUTHORIZATION': f'Token {self.token.key}',
            'HTTP_X_BUSINESS_ID': str(self.business.id),
        }
        self.run = AutomationRun.objects.create(
            business=self.business, event=Event.ORDER_PAID,
            subject_type='Order', subject_id='42',
            status=AutomationRun.STATUS_FAILED, error='whatsapp timed out',
            actions=['automation.handlers.notify_order_status'],
            completed_at=timezone.now(),
        )

    def get(self, path, **extra):
        return self.client.get(path, **{**self.headers, **extra})

    def test_the_list_is_scoped_to_the_active_business(self):
        rival = self.other_business()
        AutomationRun.objects.create(business=rival, event=Event.ORDER_PAID)

        response = self.get('/api/v1/automation/runs/')
        self.assertEqual(response.status_code, 200)
        results = response.json()['results']
        self.assertEqual([r['id'] for r in results], [str(self.run.pk)])

    def test_a_run_carries_its_error_and_duration(self):
        response = self.get(f'/api/v1/automation/runs/{self.run.pk}/')
        body = response.json()

        self.assertEqual(body['status'], AutomationRun.STATUS_FAILED)
        self.assertEqual(body['error'], 'whatsapp timed out')
        self.assertIsNotNone(body['duration_ms'])
        self.assertEqual(body['event_display'], 'Order paid')

    def test_a_pending_run_reports_no_duration(self):
        pending = AutomationRun.objects.create(
            business=self.business, event=Event.ORDER_CREATED,
        )
        response = self.get(f'/api/v1/automation/runs/{pending.pk}/')
        self.assertIsNone(response.json()['duration_ms'])

    def test_filters_narrow_the_list(self):
        AutomationRun.objects.create(
            business=self.business, event=Event.CART_ABANDONED,
            status=AutomationRun.STATUS_SUCCESS, subject_id='99',
        )

        self.assertEqual(len(self.get('/api/v1/automation/runs/?failed=true').json()['results']), 1)
        self.assertEqual(
            len(self.get(f'/api/v1/automation/runs/?event={Event.CART_ABANDONED}').json()['results']), 1,
        )
        self.assertEqual(len(self.get('/api/v1/automation/runs/?subject_id=42').json()['results']), 1)
        self.assertEqual(len(self.get('/api/v1/automation/runs/?status=SKIPPED').json()['results']), 0)

    def test_stuck_only_counts_runs_old_enough_to_be_stranded(self):
        """A run queued a second ago is in flight, not stranded — flagging it
        would make the alarm meaningless."""
        fresh = AutomationRun.objects.create(
            business=self.business, event=Event.ORDER_CREATED,
        )
        self.assertEqual(len(self.get('/api/v1/automation/runs/?stuck=true').json()['results']), 0)

        self.backdate(AutomationRun, fresh.pk, 'created_at', hours=2)
        stranded = self.get('/api/v1/automation/runs/?stuck=true').json()['results']
        self.assertEqual([r['id'] for r in stranded], [str(fresh.pk)])

    def test_the_window_filter_excludes_older_runs(self):
        self.backdate(AutomationRun, self.run.pk, 'created_at', days=10)

        self.assertEqual(len(self.get('/api/v1/automation/runs/?hours=24').json()['results']), 0)
        self.assertEqual(len(self.get('/api/v1/automation/runs/?hours=720').json()['results']), 1)

    def test_a_junk_window_falls_back_instead_of_500ing(self):
        response = self.get('/api/v1/automation/runs/?hours=lots')
        self.assertEqual(response.status_code, 200)

    def test_summary_reports_failures_and_stranded_runs(self):
        stranded = AutomationRun.objects.create(
            business=self.business, event=Event.ORDER_CREATED,
        )
        self.backdate(AutomationRun, stranded.pk, 'created_at', hours=3)

        body = self.get('/api/v1/automation/runs/summary/').json()

        self.assertEqual(body['failed'], 1)
        self.assertEqual(body['stuck'], 1)
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['by_status'][AutomationRun.STATUS_FAILED], 1)
        self.assertEqual(body['by_event'][0]['n'], 1)

    def test_summary_counts_nothing_from_another_business(self):
        rival = self.other_business()
        for _ in range(3):
            AutomationRun.objects.create(
                business=rival, event=Event.ORDER_PAID,
                status=AutomationRun.STATUS_FAILED,
            )

        body = self.get('/api/v1/automation/runs/summary/').json()
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['failed'], 1)

    def test_the_event_filter_lists_the_whole_vocabulary(self):
        """Served from Event.CHOICES, so an event that has never fired is still
        selectable — an empty result is an answer."""
        body = self.get('/api/v1/automation/runs/events/').json()
        self.assertEqual(len(body), len(Event.CHOICES))
        self.assertIn(
            {'value': Event.FEEDBACK_DUE, 'label': 'Feedback due'}, body,
        )

    def test_retry_requeues_a_failed_run(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f'/api/v1/automation/runs/{self.run.pk}/retry/', **self.headers
            )

        self.assertEqual(response.status_code, 200)
        self.run.refresh_from_db()
        # The eager worker picked it straight back up and the handler ran.
        self.assertEqual(self.run.status, AutomationRun.STATUS_SUCCESS)
        self.assertEqual(self.run.error, '')

    def test_retry_clears_the_previous_failure_before_re_running(self):
        with patch('automation.tasks.run_automation.delay'):
            response = self.client.post(
                f'/api/v1/automation/runs/{self.run.pk}/retry/', **self.headers
            )

        body = response.json()
        self.assertEqual(body['status'], AutomationRun.STATUS_PENDING)
        self.assertEqual(body['error'], '')
        self.assertEqual(body['actions'], [])
        self.assertIsNone(body['completed_at'])

    def test_a_successful_run_is_not_retryable(self):
        """The idempotency key guards emitting, not dispatching — nothing would
        stop a second receipt going out."""
        done = AutomationRun.objects.create(
            business=self.business, event=Event.ORDER_PAID,
            status=AutomationRun.STATUS_SUCCESS, completed_at=timezone.now(),
        )
        response = self.client.post(
            f'/api/v1/automation/runs/{done.pk}/retry/', **self.headers
        )
        self.assertEqual(response.status_code, 400)

    def test_retry_is_refused_for_a_cashier(self):
        from rest_framework.authtoken.models import Token

        cashier = User.objects.create_user(
            username='cashier', email='cashier@test.local',
            password='Str0ng!Passw0rd123', phone='0755000000',
        )
        UserBusinessMembership.objects.create(
            user=cashier, business=self.business, role='CASHIER', is_active=True,
        )
        headers = {
            'HTTP_AUTHORIZATION': f'Token {Token.objects.create(user=cashier).key}',
            'HTTP_X_BUSINESS_ID': str(self.business.id),
        }

        # Reading the trail is fine for any member; replaying is not.
        self.assertEqual(self.client.get('/api/v1/automation/runs/', **headers).status_code, 200)
        self.assertEqual(
            self.client.post(f'/api/v1/automation/runs/{self.run.pk}/retry/', **headers).status_code,
            403,
        )

    def test_another_tenants_run_is_not_reachable_by_id(self):
        rival = self.other_business()
        theirs = AutomationRun.objects.create(business=rival, event=Event.ORDER_PAID)

        self.assertEqual(self.get(f'/api/v1/automation/runs/{theirs.pk}/').status_code, 404)
        self.assertEqual(
            self.client.post(
                f'/api/v1/automation/runs/{theirs.pk}/retry/', **self.headers
            ).status_code,
            404,
        )

    def test_the_endpoint_is_closed_to_anonymous_callers(self):
        self.assertEqual(self.client.get('/api/v1/automation/runs/').status_code, 401)

    def test_runs_are_not_writable_through_the_api(self):
        """The trail is a record, not an input."""
        response = self.client.post(
            '/api/v1/automation/runs/',
            data={'event': Event.ORDER_PAID}, content_type='application/json',
            **self.headers,
        )
        self.assertEqual(response.status_code, 405)


class AutomationPageTests(TestCase):
    """The page is a shell — data arrives from the API — so the only server-side
    behaviour worth testing is that it renders and is wired into the nav."""

    def test_the_page_renders_with_its_script(self):
        response = self.client.get('/dashboard/automation/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'js/automation.js')

    def test_the_nav_links_to_it(self):
        response = self.client.get('/dashboard/')
        self.assertContains(response, 'href="/dashboard/automation/"')


# ---------------------------------------------------------------------------
# Celery / Redis wiring
# ---------------------------------------------------------------------------

class CeleryConfigTests(TestCase):
    """Guards on the configuration itself. Each of these has a failure mode
    that would not show up until production."""

    def test_the_suite_runs_without_a_broker_or_redis(self):
        from django.conf import settings

        self.assertTrue(settings.CELERY_TASK_ALWAYS_EAGER)
        self.assertTrue(settings.CELERY_TASK_EAGER_PROPAGATES)
        # A Redis-backed cache would make DRF throttling — which reads the
        # cache on every request — fail every API test when Redis is down.
        self.assertIn('locmem', settings.CACHES['default']['BACKEND'])

    def test_pickle_is_not_an_accepted_content_type(self):
        """Anything that can write to Redis could otherwise run code here."""
        from django.conf import settings

        self.assertEqual(settings.CELERY_ACCEPT_CONTENT, ['json'])
        self.assertEqual(settings.CELERY_TASK_SERIALIZER, 'json')

    def test_publishing_fails_fast_instead_of_blocking_the_request(self):
        """emit() runs inside the HTTP request; Celery's default retry policy
        would block it for about a minute with the broker down."""
        from django.conf import settings

        self.assertLessEqual(settings.CELERY_TASK_PUBLISH_RETRY_POLICY['max_retries'], 3)
        self.assertLessEqual(
            settings.CELERY_BROKER_TRANSPORT_OPTIONS['socket_connect_timeout'], 5,
        )

    def test_every_scheduled_task_exists(self):
        """A typo in CELERY_BEAT_SCHEDULE is only discovered when beat fires
        the entry and the worker reports an unregistered task."""
        from django.conf import settings

        from core.celery import app

        self.assertTrue(settings.CELERY_BEAT_SCHEDULE)
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            with self.subTest(schedule=name):
                self.assertIn(entry['task'], app.tasks)

    def test_the_dispatch_task_is_registered(self):
        from core.celery import app

        self.assertIn('automation.tasks.run_automation', app.tasks)

    def test_every_event_choice_is_unique(self):
        """Event values are stored on the row; a duplicated or renamed constant
        silently orphans historical runs."""
        values = [value for value, _ in Event.CHOICES]
        self.assertEqual(len(values), len(set(values)))
