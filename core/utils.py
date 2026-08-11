import secrets
import string
import uuid
from datetime import datetime

# Unambiguous alphabet: no O/0 or I/1, so numbers survive being read aloud or
# typed into an M-Pesa bill-reference field.
_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


def _random_code(length):
    # secrets, not random: order numbers double as M-Pesa account references,
    # so they should not be guessable from a previously issued one.
    return ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def generate_order_number(prefix='ORD'):
    """Generate a unique order number.

    Format: ORD<YYYYMMDDHHMMSS><8 random chars>. The random tail was 4 decimal
    digits, which collided in practice — 25 orders inside one second hit the
    order_number UNIQUE constraint roughly 3% of the time. 31**8 combinations
    per second makes that negligible, and Order.save() still retries if the
    constraint fires.
    """
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    return f"{prefix}{timestamp}{_random_code(8)}"


def generate_transaction_id(prefix='TXN'):
    """Generate a unique transaction ID"""
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    return f"{prefix}{timestamp}{_random_code(8)}"


def generate_sku(prefix='SKU'):
    """Generate a unique SKU"""
    unique_id = str(uuid.uuid4()).replace('-', '')[:8].upper()
    return f"{prefix}-{unique_id}"

def format_currency(amount, currency='KSh'):
    """Format currency amount"""
    return f"{currency} {amount:,.2f}"

def validate_phone_number(phone):
    """Validate Kenyan phone number format"""
    import re
    # Remove any whitespace
    phone = re.sub(r'\s+', '', phone)
    # Check format: 07xxxxxxxx or 071xxxxxxxx or +2547xxxxxxxx
    pattern = r'^(0|\+254)7\d{8}$'
    return re.match(pattern, phone) is not None

def get_client_ip(request):
    """Get client IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip