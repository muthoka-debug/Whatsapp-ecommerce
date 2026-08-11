"""Model fields that encrypt their value at rest.

Used for third-party credentials we must be able to read back in plaintext to
call an API with (WhatsApp access tokens, M-Pesa consumer secrets and passkeys).
Hashing is not an option for those, so they are encrypted symmetrically with
Fernet (AES-128-CBC + HMAC) using ``settings.FIELD_ENCRYPTION_KEY``.

If no key is configured the value is stored as plaintext and a warning is
logged. That keeps local development frictionless while making the production
misconfiguration loud.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.functional import cached_property

logger = logging.getLogger(__name__)

# Marks a stored value as ciphertext so we can tell encrypted rows apart from
# rows written before a key was configured.
ENCRYPTED_PREFIX = 'enc$v1$'


class EncryptedTextField(models.TextField):
    """A TextField whose database value is Fernet-encrypted.

    Reads transparently decrypt. Values written while no key was configured stay
    readable after a key is added, so enabling encryption is a non-breaking
    change; they are re-encrypted the next time the row is saved.
    """

    @cached_property
    def _fernet(self):
        from django.conf import settings

        key = getattr(settings, 'FIELD_ENCRYPTION_KEY', '')
        if not key:
            logger.warning(
                'FIELD_ENCRYPTION_KEY is not set — %s.%s will be stored in '
                'plaintext. Set it before going to production.',
                self.model._meta.label if hasattr(self, 'model') else '?',
                self.name,
            )
            return None
        try:
            return Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise ImproperlyConfigured(
                'FIELD_ENCRYPTION_KEY is not a valid Fernet key. Generate one '
                'with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        fernet = self._fernet
        if fernet is None:
            return value
        # Never double-encrypt an already-encrypted value.
        if value.startswith(ENCRYPTED_PREFIX):
            return value
        return ENCRYPTED_PREFIX + fernet.encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        return self._decrypt(value)

    def to_python(self, value):
        return self._decrypt(super().to_python(value))

    def _decrypt(self, value):
        if value in (None, '') or not isinstance(value, str):
            return value
        if not value.startswith(ENCRYPTED_PREFIX):
            # Written before a key was configured — hand it back as-is.
            return value
        fernet = self._fernet
        if fernet is None:
            logger.error(
                'Found encrypted data in %s but FIELD_ENCRYPTION_KEY is unset; '
                'cannot decrypt.', self.name,
            )
            return ''
        try:
            return fernet.decrypt(value[len(ENCRYPTED_PREFIX):].encode()).decode()
        except InvalidToken:
            logger.error(
                'Failed to decrypt %s — the FIELD_ENCRYPTION_KEY has probably '
                'changed since this row was written.', self.name,
            )
            return ''
