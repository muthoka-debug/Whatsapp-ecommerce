import logging
from types import SimpleNamespace

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import Notification, NotificationTemplate

logger = logging.getLogger(__name__)


class NotificationService:
    """Sends notifications across email, SMS, WhatsApp and in-app channels."""

    def __init__(self, business=None):
        self.business = business

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def send_notification(self, notification):
        """Dispatch one notification. Returns True when it was delivered."""
        try:
            if notification.channel == 'EMAIL':
                self.send_email(notification)
            elif notification.channel == 'SMS':
                self.send_sms(notification)
            elif notification.channel == 'WHATSAPP':
                self.send_whatsapp(notification)
            elif notification.channel == 'IN_APP':
                self.mark_in_app(notification)
            else:
                logger.warning(
                    'Unknown notification channel %r', notification.channel
                )
                return False

            notification.is_sent = True
            notification.sent_at = timezone.now()
            notification.save(update_fields=['is_sent', 'sent_at'])
            return True
        except Exception:
            logger.exception(
                'Failed to send notification %s over %s',
                notification.pk,
                notification.channel,
            )
            return False

    def send_email(self, notification):
        recipient_email = getattr(notification.recipient, 'email', '')
        if not recipient_email:
            raise ValueError('Recipient has no email address')

        send_mail(
            subject=notification.title,
            message=notification.message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient_email],
            fail_silently=False,
        )

    def send_sms(self, notification):
        """Send an SMS via Twilio."""
        # Imported lazily so the app still boots when twilio is absent.
        from twilio.rest import Client

        phone = getattr(notification.recipient, 'phone', '')
        if not phone:
            raise ValueError('Recipient has no phone number')

        if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
            raise ValueError('Twilio credentials are not configured')

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=notification.message[:160],  # SMS length limit
            from_=settings.TWILIO_PHONE_NUMBER,
            to=phone,
        )
        logger.info('SMS sent: %s', message.sid)

    def send_whatsapp(self, notification):
        """Send over the WhatsApp Business API and record the message.

        Delivery goes through ``whatsapp_integration.messaging`` so that a
        notification reaching a customer outside Meta's 24-hour window is sent
        as an approved template instead of failing. The template to fall back on
        comes from ``notification.template_category``, set by the event methods
        below; a notification without one can only be delivered inside the
        window.
        """
        from whatsapp_integration.messaging import get_conversation, send_to_customer
        from whatsapp_integration.models import WhatsAppMessage

        phone = getattr(notification.recipient, 'phone', '')
        if not phone:
            raise ValueError('Recipient has no phone number')

        conversation = get_conversation(self.business, phone)

        delivery = send_to_customer(
            self.business,
            phone,
            notification.message,
            template_category=getattr(notification, 'template_category', None),
            template_context=getattr(notification, 'template_context', None),
            conversation=conversation,
        )

        if conversation:
            WhatsAppMessage.objects.create(
                business=self.business,
                conversation=conversation,
                message_id=(
                    delivery.message_id or f'NOTIF-{timezone.now().timestamp()}'
                ),
                direction='OUTGOING',
                message_type='TEMPLATE' if delivery.used_template else 'TEXT',
                content=notification.message,
                sent_at=timezone.now(),
                is_ai_generated=False,
                metadata=(
                    {'template_name': delivery.template_name}
                    if delivery.used_template
                    else {}
                ),
            )

        logger.info(
            'WhatsApp notification sent to %s (%s)',
            phone,
            f'template {delivery.template_name}'
            if delivery.used_template
            else 'free-form',
        )

    def mark_in_app(self, notification):
        """In-app notification is already persisted; nothing more to do."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_recipient_user(self, *candidates):
        """Return the first candidate that is a User we can notify.

        Callers pass a mix of Users (order.created_by, payment.processed_by),
        Customers (order.customer) and memberships. Notification.recipient is a
        FK to User, so anything else has to be mapped or skipped — the previous
        code blindly did ``recipient.user`` and crashed on a User or Customer.
        """
        from django.contrib.auth import get_user_model

        user_model = get_user_model()

        for candidate in candidates:
            if candidate is None:
                continue
            if isinstance(candidate, user_model):
                return candidate
            # UserBusinessMembership and similar wrappers.
            nested = getattr(candidate, 'user', None)
            if isinstance(nested, user_model):
                return nested

        # Fall back to the business owner so the alert is not lost.
        if self.business is not None:
            owner_membership = self.business.members.filter(
                role='OWNER', is_active=True
            ).select_related('user').first()
            if owner_membership:
                return owner_membership.user
            return self.business.owner

        return None

    def _render(self, template_type, context, fallback_title, fallback_message):
        template = NotificationTemplate.objects.filter(
            business=self.business,
            template_type=template_type,
            is_active=True,
        ).first()

        if template:
            return template.subject, template.render(context)
        return fallback_title, fallback_message

    def _deliver_to_customer_over_whatsapp(
        self, notification, customer, template_category=None, template_context=None
    ):
        """Mirror a stored notification out to the customer over WhatsApp.

        The stored Notification's recipient is the staff user (its FK only
        accepts a User), so delivery to the *customer* goes through a lightweight
        stand-in carrying just the fields send_whatsapp() reads. The old code
        flipped notification.channel in memory and re-sent it, which delivered
        the customer's message to the staff member instead.

        ``template_category`` names the approved template to use once the
        customer's 24-hour window has closed. Without one the send is limited to
        customers who messaged in the last day.
        """
        from whatsapp_integration.messaging import OutsideServiceWindow

        if customer is None or not getattr(customer, 'phone', ''):
            return

        outbound = SimpleNamespace(
            pk=notification.pk,
            recipient=customer,
            title=notification.title,
            message=notification.message,
            channel='WHATSAPP',
            template_category=template_category,
            template_context=template_context,
        )
        try:
            self.send_whatsapp(outbound)
        except OutsideServiceWindow as exc:
            # Expected whenever a customer has not written in for a day and the
            # business has not registered a template: not a bug, but the message
            # did not reach them, so say so plainly rather than as a traceback.
            logger.warning(
                'WhatsApp delivery skipped for notification %s: %s',
                notification.pk,
                exc,
            )
        except Exception:
            logger.exception(
                'WhatsApp delivery failed for notification %s', notification.pk
            )

    # ------------------------------------------------------------------
    # Event notifications
    # ------------------------------------------------------------------

    def send_order_confirmation(self, order):
        """Notify staff, and optionally the customer, that an order was placed."""
        recipient = self._resolve_recipient_user(order.created_by)
        if not recipient:
            logger.warning('No recipient for order confirmation %s', order.pk)
            return None

        currency = getattr(self.business, 'currency', '')
        context = {
            'order_number': order.order_number,
            'customer_name': order.customer.name,
            'total_amount': order.total_amount,
            'currency': currency,
        }
        title, message = self._render(
            'ORDER_CONFIRMATION',
            context,
            f'Order #{order.order_number} Confirmed',
            f'Order #{order.order_number} has been confirmed. '
            f'Total: {currency} {order.total_amount}',
        )

        notification = Notification.objects.create(
            business=self.business,
            recipient=recipient,
            title=title,
            message=message,
            channel='IN_APP',
            priority='HIGH',
            metadata={'order_id': str(order.id)},
        )

        if getattr(self.business, 'whatsapp_auto_order_confirmation', False):
            self._deliver_to_customer_over_whatsapp(
                notification, order.customer, 'ORDER_CONFIRMATION', context
            )

        return notification

    def send_payment_confirmation(self, payment):
        """Notify that a payment completed."""
        order = payment.order
        recipient = self._resolve_recipient_user(
            payment.processed_by,
            order.created_by if order else None,
        )
        if not recipient:
            logger.warning('No recipient for payment confirmation %s', payment.pk)
            return None

        currency = getattr(self.business, 'currency', '')
        context = {
            'amount': payment.amount,
            'currency': currency,
            'method': payment.get_method_display(),
            'transaction_id': payment.transaction_id,
            'order_number': order.order_number if order else '',
        }
        title, message = self._render(
            'PAYMENT_CONFIRMATION',
            context,
            'Payment Confirmed',
            f'Payment of {currency} {payment.amount} received via '
            f'{payment.get_method_display()}',
        )

        notification = Notification.objects.create(
            business=self.business,
            recipient=recipient,
            title=title,
            message=message,
            channel='IN_APP',
            priority='HIGH',
            metadata={'payment_id': str(payment.id)},
        )

        if getattr(self.business, 'whatsapp_auto_order_confirmation', False):
            self._deliver_to_customer_over_whatsapp(
                notification,
                order.customer if order else None,
                'PAYMENT_CONFIRMATION',
                context,
            )

        return notification

    # Wording per delivery status, used when no DELIVERY_UPDATE template exists.
    DELIVERY_MESSAGES = {
        'ASSIGNED': 'Order #{order_number} has been assigned for delivery.',
        'PICKED_UP': 'Order #{order_number} has been picked up.',
        'IN_TRANSIT': 'Order #{order_number} is out for delivery.',
        'DELIVERED': 'Order #{order_number} has been delivered. Thank you!',
        'FAILED': 'Delivery of order #{order_number} failed. We will be in touch.',
        'CANCELLED': 'Delivery of order #{order_number} has been cancelled.',
        'created': 'Delivery for order #{order_number} has been scheduled.',
    }

    def send_delivery_update(self, delivery, event=None):
        """Notify that a delivery was created or changed status.

        ``event`` is the delivery status, or the literal 'created'.
        """
        order = delivery.order
        recipient = self._resolve_recipient_user(order.created_by)
        if not recipient:
            logger.warning('No recipient for delivery update %s', delivery.pk)
            return None

        key = event or delivery.status
        fallback = self.DELIVERY_MESSAGES.get(
            key, 'Delivery status for order #{order_number} is now ' + str(key) + '.'
        ).format(order_number=order.order_number)

        context = {
            'order_number': order.order_number,
            'customer_name': order.customer.name,
            'status': delivery.get_status_display(),
            'address': delivery.address,
            'fee': delivery.fee,
            'currency': getattr(self.business, 'currency', ''),
            'tracking_number': delivery.tracking_number,
        }
        title, message = self._render(
            'DELIVERY_UPDATE',
            context,
            f'Delivery Update - Order #{order.order_number}',
            fallback,
        )

        notification = Notification.objects.create(
            business=self.business,
            recipient=recipient,
            title=title,
            message=message,
            channel='IN_APP',
            priority='MEDIUM',
            metadata={
                'delivery_id': str(delivery.id),
                'order_id': str(order.id),
                'status': delivery.status,
            },
        )

        # The customer only wants the milestones, not every internal move.
        if key in ('IN_TRANSIT', 'DELIVERED') and getattr(
            self.business, 'whatsapp_delivery_updates', False
        ):
            self._deliver_to_customer_over_whatsapp(
                notification, order.customer, 'DELIVERY_UPDATE', context
            )

        return notification

    def send_order_status_update(self, order, old_status, new_status):
        """Notify that an order changed status."""
        recipient = self._resolve_recipient_user(order.created_by)
        if not recipient:
            logger.warning('No recipient for status update on order %s', order.pk)
            return None

        context = {
            'order_number': order.order_number,
            'old_status': old_status,
            'new_status': new_status,
            'customer_name': order.customer.name,
        }
        title, message = self._render(
            'ORDER_STATUS',
            context,
            f'Order #{order.order_number} Updated',
            f'Order #{order.order_number} status changed from '
            f'{old_status} to {new_status}',
        )

        notification = Notification.objects.create(
            business=self.business,
            recipient=recipient,
            title=title,
            message=message,
            channel='IN_APP',
            priority='MEDIUM',
            metadata={
                'order_id': str(order.id),
                'old_status': old_status,
                'new_status': new_status,
            },
        )

        if getattr(self.business, 'whatsapp_order_status_updates', False):
            self._deliver_to_customer_over_whatsapp(
                notification, order.customer, 'ORDER_STATUS', context
            )

        return notification

    # ------------------------------------------------------------------
    # Automation notifications
    #
    # Driven by the automation app rather than by a user action. The two
    # customer-facing ones below are unsolicited outbound marketing, so they
    # respect whatsapp_auto_order_confirmation as the customer-contact consent
    # flag rather than sending unconditionally.
    # ------------------------------------------------------------------

    def send_payment_failed(self, payment):
        """Alert staff that a payment attempt failed."""
        order = payment.order
        recipient = self._resolve_recipient_user(
            payment.processed_by,
            order.created_by if order else None,
        )
        if not recipient:
            logger.warning('No recipient for failed payment %s', payment.pk)
            return None

        currency = getattr(self.business, 'currency', '')
        reference = order.order_number if order else payment.pk
        title, message = self._render(
            'PAYMENT_FAILED',
            {
                'order_number': reference,
                'amount': payment.amount,
                'currency': currency,
                'method': payment.method,
            },
            f'Payment failed for #{reference}',
            f'A {payment.method} payment of {currency} {payment.amount} for '
            f'#{reference} did not go through.',
        )

        return Notification.objects.create(
            business=self.business,
            recipient=recipient,
            title=title,
            message=message,
            channel='IN_APP',
            priority='HIGH',
            metadata={'payment_id': str(payment.id)},
        )

    def send_low_stock_alert(self, product):
        """Tell the owner a product needs reordering."""
        recipient = self._resolve_recipient_user()
        if not recipient:
            return None

        title, message = self._render(
            'LOW_STOCK',
            {
                'product_name': product.name,
                'stock': product.stock,
                'threshold': product.low_stock_threshold,
            },
            f'Low stock: {product.name}',
            f'{product.name} is down to {product.stock} units '
            f'(reorder threshold {product.low_stock_threshold}).',
        )

        return Notification.objects.create(
            business=self.business,
            recipient=recipient,
            title=title,
            message=message,
            channel='IN_APP',
            priority='HIGH',
            metadata={'product_id': str(product.id), 'stock': product.stock},
        )

    def send_abandoned_cart_reminder(self, cart):
        """Nudge a customer who left items in their cart."""
        customer = cart.customer
        if customer is None or not getattr(customer, 'phone', ''):
            return None

        recipient = self._resolve_recipient_user()
        if not recipient:
            return None

        item_count = cart.items.count()
        context = {'customer_name': customer.name, 'item_count': item_count}
        title, message = self._render(
            'ABANDONED_CART',
            context,
            'You left something behind',
            f'Hi {customer.name}, you still have {item_count} item(s) waiting '
            f'in your cart. Reply here and we will help you check out.',
        )

        notification = Notification.objects.create(
            business=self.business,
            recipient=recipient,
            title=title,
            message=message,
            channel='IN_APP',
            priority='LOW',
            metadata={'cart_id': str(cart.id)},
        )

        if getattr(self.business, 'whatsapp_auto_order_confirmation', False):
            self._deliver_to_customer_over_whatsapp(
                notification, customer, 'ABANDONED_CART', context
            )

        return notification

    def send_feedback_request(self, order):
        """Ask a customer how a delivered order went."""
        customer = order.customer
        recipient = self._resolve_recipient_user(order.created_by)
        if not recipient:
            return None

        context = {
            'order_number': order.order_number,
            'customer_name': customer.name,
        }
        title, message = self._render(
            'FEEDBACK_REQUEST',
            context,
            f'How was order #{order.order_number}?',
            f'Hi {customer.name}, your order #{order.order_number} was '
            f'delivered. How did we do? Just reply to this message.',
        )

        notification = Notification.objects.create(
            business=self.business,
            recipient=recipient,
            title=title,
            message=message,
            channel='IN_APP',
            priority='LOW',
            metadata={'order_id': str(order.id)},
        )

        if getattr(self.business, 'whatsapp_order_status_updates', False):
            self._deliver_to_customer_over_whatsapp(
                notification, customer, 'FEEDBACK_REQUEST', context
            )

        return notification
