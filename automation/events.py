"""The automation event vocabulary.

An event is a statement of fact about something that already happened in the
database — never an instruction. `ORDER_PAID` means the row is written and
committed; what should follow is the dispatcher's decision, not the caller's.
That split is what keeps business logic out of views: `orders` announces, and
`automation` decides.

Adding an event: add the constant, add it to CHOICES, then register handlers in
services.HANDLERS. Emitting an event nothing handles is harmless by design —
it is logged and ignored — so producers can be wired before consumers exist.
"""


class Event:
    """Event names. Values are stored in AutomationRun, so treat them as data:
    rename one and historical rows stop matching."""

    # Order lifecycle
    ORDER_CREATED = 'ORDER_CREATED'
    ORDER_CONFIRMED = 'ORDER_CONFIRMED'
    ORDER_PAID = 'ORDER_PAID'
    ORDER_PROCESSING = 'ORDER_PROCESSING'
    ORDER_READY = 'ORDER_READY'
    ORDER_SHIPPED = 'ORDER_SHIPPED'
    ORDER_DELIVERED = 'ORDER_DELIVERED'
    ORDER_CANCELLED = 'ORDER_CANCELLED'

    # Payments
    PAYMENT_COMPLETED = 'PAYMENT_COMPLETED'
    PAYMENT_FAILED = 'PAYMENT_FAILED'

    # Delivery lifecycle
    DELIVERY_ASSIGNED = 'DELIVERY_ASSIGNED'
    DELIVERY_OUT_FOR_DELIVERY = 'DELIVERY_OUT_FOR_DELIVERY'
    DELIVERY_DELIVERED = 'DELIVERY_DELIVERED'
    DELIVERY_FAILED = 'DELIVERY_FAILED'

    # Inventory
    PRODUCT_STOCK_LOW = 'PRODUCT_STOCK_LOW'
    PRODUCT_OUT_OF_STOCK = 'PRODUCT_OUT_OF_STOCK'

    # Customer lifecycle
    CART_ABANDONED = 'CART_ABANDONED'
    CUSTOMER_INACTIVE = 'CUSTOMER_INACTIVE'
    FEEDBACK_DUE = 'FEEDBACK_DUE'

    CHOICES = [
        (ORDER_CREATED, 'Order created'),
        (ORDER_CONFIRMED, 'Order confirmed'),
        (ORDER_PAID, 'Order paid'),
        (ORDER_PROCESSING, 'Order processing'),
        (ORDER_READY, 'Order ready'),
        (ORDER_SHIPPED, 'Order shipped'),
        (ORDER_DELIVERED, 'Order delivered'),
        (ORDER_CANCELLED, 'Order cancelled'),
        (PAYMENT_COMPLETED, 'Payment completed'),
        (PAYMENT_FAILED, 'Payment failed'),
        (DELIVERY_ASSIGNED, 'Delivery assigned'),
        (DELIVERY_OUT_FOR_DELIVERY, 'Delivery out for delivery'),
        (DELIVERY_DELIVERED, 'Delivery delivered'),
        (DELIVERY_FAILED, 'Delivery failed'),
        (PRODUCT_STOCK_LOW, 'Product stock low'),
        (PRODUCT_OUT_OF_STOCK, 'Product out of stock'),
        (CART_ABANDONED, 'Cart abandoned'),
        (CUSTOMER_INACTIVE, 'Customer inactive'),
        (FEEDBACK_DUE, 'Feedback due'),
    ]


# Order status -> event. The order app announces a transition; this table is
# the single place that decides which of them are worth automating.
ORDER_STATUS_EVENTS = {
    'CONFIRMED': Event.ORDER_CONFIRMED,
    'PAID': Event.ORDER_PAID,
    'PROCESSING': Event.ORDER_PROCESSING,
    'READY': Event.ORDER_READY,
    'OUT_FOR_DELIVERY': Event.ORDER_SHIPPED,
    'DELIVERED': Event.ORDER_DELIVERED,
    'CANCELLED': Event.ORDER_CANCELLED,
}

DELIVERY_STATUS_EVENTS = {
    'ASSIGNED': Event.DELIVERY_ASSIGNED,
    'IN_TRANSIT': Event.DELIVERY_OUT_FOR_DELIVERY,
    'DELIVERED': Event.DELIVERY_DELIVERED,
    'FAILED': Event.DELIVERY_FAILED,
}
