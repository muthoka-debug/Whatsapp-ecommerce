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
        """Send over the WhatsApp Business API and record the message."""
        from whatsapp_integration.client import WhatsAppClient
        from whatsapp_integration.models import (
            WhatsAppConversation,
            WhatsAppMessage,
            WhatsAppSetting,
        )

        phone = getattr(notification.recipient, 'phone', '')
        if not phone:
            raise ValueError('Recipient has no phone number')

        whatsapp_settings = WhatsAppSetting.objects.get(business=self.business)

        conversation = WhatsAppConversation.objects.filter(
            business=self.business,
            phone_number=phone,
        ).first()

        provider_message_id = WhatsAppClient(whatsapp_settings).send_text(
            phone, notification.message
        )

        if conversation:
            WhatsAppMessage.objects.create(
                business=self.business,
                conversation=conversation,
                message_id=(
                    provider_message_id or f'NOTIF-{timezone.now().timestamp()}'
                ),
                direction='OUTGOING',
                message_type='TEXT',
                content=notification.message,
                sent_at=timezone.now(),
                is_ai_generated=False,
            )

        logger.info('WhatsApp notification sent to %s', phone)

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

    def _deliver_to_customer_over_whatsapp(self, notification, customer):
        """Mirror a stored notification out to the customer over WhatsApp.

        The stored Notification's recipient is the staff user (its FK only
        accepts a User), so delivery to the *customer* goes through a lightweight
        stand-in carrying just the fields send_whatsapp() reads. The old code
        flipped notification.channel in memory and re-sent it, which delivered
        the customer's message to the staff member instead.
        """
        if customer is None or not getattr(customer, 'phone', ''):
            return

        outbound = SimpleNamespace(
            pk=notification.pk,
            recipient=customer,
            title=notification.title,
            message=notification.message,
            channel='WHATSAPP',
        )
        try:
            self.send_whatsapp(outbound)
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
        title, message = self._render(
            'ORDER_CONFIRMATION',
            {
                'order_number': order.order_number,
                'customer_name': order.customer.name,
                'total_amount': order.total_amount,
                'currency': currency,
            },
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
            self._deliver_to_customer_over_whatsapp(notification, order.customer)

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
        title, message = self._render(
            'PAYMENT_CONFIRMATION',
            {
                'amount': payment.amount,
                'currency': currency,
                'method': payment.get_method_display(),
                'transaction_id': payment.transaction_id,
            },
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
                notification, order.customer if order else None
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

        title, message = self._render(
            'DELIVERY_UPDATE',
            {
                'order_number': order.order_number,
                'customer_name': order.customer.name,
                'status': delivery.get_status_display(),
                'address': delivery.address,
                'fee': delivery.fee,
                'currency': getattr(self.business, 'currency', ''),
                'tracking_number': delivery.tracking_number,
            },
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
            self._deliver_to_customer_over_whatsapp(notification, order.customer)

        return notification

    def send_order_status_update(self, order, old_status, new_status):
        """Notify that an order changed status."""
        recipient = self._resolve_recipient_user(order.created_by)
        if not recipient:
            logger.warning('No recipient for status update on order %s', order.pk)
            return None

        title, message = self._render(
            'ORDER_STATUS',
            {
                'order_number': order.order_number,
                'old_status': old_status,
                'new_status': new_status,
                'customer_name': order.customer.name,
            },
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
            self._deliver_to_customer_over_whatsapp(notification, order.customer)

        return notification
