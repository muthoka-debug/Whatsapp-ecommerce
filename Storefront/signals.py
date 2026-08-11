import datetime

from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Cart


@receiver(pre_save, sender=Cart)
def set_cart_expiry(sender, instance, **kwargs):
    """Default new carts to a 7-day expiry.

    pre_save rather than post_save: setting the field before the INSERT avoids
    the extra UPDATE round-trip the post_save version needed, and avoids
    re-entering save() from inside a save().
    """
    if instance.expires_at is None:
        instance.expires_at = timezone.now() + datetime.timedelta(days=7)
