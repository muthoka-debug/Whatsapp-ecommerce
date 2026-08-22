"""Tests for the WhatsApp webhook endpoint.

The subscription handshake is the one part of this integration that cannot be
debugged from our side: Meta reports a failure as "The URL couldn't be
validated" with no detail, so a response that is subtly wrong looks identical to
an unreachable server. These tests pin the two things Meta actually checks —
the exact response body, and that a wrong token is refused.
"""

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from businesses.models import Business

from .models import WhatsAppSetting
from .views import _settings_for_metadata


def make_business(name, *, username, phone):
    owner = User.objects.create_user(
        username=username, email=f'{username}@test.local',
        password='Str0ng!Passw0rd123', phone=phone,
    )
    return Business.objects.create(
        name=name, phone=phone, email=f'{username}-shop@test.local',
        address='Nairobi', owner=owner,
    )


class WebhookVerificationTests(TestCase):
    """Meta's GET handshake at /api/v1/whatsapp/webhook/."""

    @classmethod
    def setUpTestData(cls):
        cls.business = make_business(
            'Test Shop', username='wa-owner', phone='0712345678',
        )
        cls.setting = WhatsAppSetting.objects.create(
            business=cls.business,
            phone_number_id='123456789',
            business_account_id='987654321',
            access_token='test-token',
            display_phone_number='15550123456',
            display_name='Test Shop',
            webhook_verify_token='correct-verify-token',
        )
        cls.url = reverse('whatsapp-webhook')

    def test_challenge_is_echoed_verbatim(self):
        """The body must be the bare challenge — no JSON quotes, no whitespace.

        Meta compares the response body to the challenge it sent, so a DRF
        Response('1158201444') renders "1158201444" and fails verification even
        though the status is 200.
        """
        response = self.client.get(self.url, {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'correct-verify-token',
            'hub.challenge': '1158201444',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'1158201444')

    def test_wrong_token_is_refused(self):
        response = self.client.get(self.url, {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'not-the-token',
            'hub.challenge': '1158201444',
        })

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b'1158201444', response.content)

    def test_missing_token_is_refused(self):
        response = self.client.get(self.url, {'hub.challenge': '1158201444'})

        self.assertEqual(response.status_code, 403)

    def test_token_resolves_across_businesses(self):
        """A second business's token must not verify the first's subscription."""
        other = make_business('Other Shop', username='wa-rival', phone='0799000000')
        WhatsAppSetting.objects.create(
            business=other,
            phone_number_id='222222222',
            business_account_id='333333333',
            access_token='other-token',
            display_phone_number='15550999888',
            display_name='Other Shop',
            webhook_verify_token='other-verify-token',
        )

        for token in ('correct-verify-token', 'other-verify-token'):
            with self.subTest(token=token):
                response = self.client.get(self.url, {
                    'hub.mode': 'subscribe',
                    'hub.verify_token': token,
                    'hub.challenge': '42',
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'42')


class WebhookRoutingTests(TestCase):
    """Working out which business an inbound payload belongs to.

    Meta's ``metadata.display_phone_number`` arrives as bare digits, while the
    number an operator copies off the API Setup page carries a leading "+".
    Matching those two strings directly drops every inbound message with a
    single "No WhatsApp settings" line in the log, so routing keys off
    ``phone_number_id`` and falls back to a digits-only comparison.
    """

    @classmethod
    def setUpTestData(cls):
        cls.business = make_business(
            'Routing Shop', username='wa-routing', phone='0722345678',
        )
        cls.setting = WhatsAppSetting.objects.create(
            business=cls.business,
            phone_number_id='555000111222333',
            business_account_id='444555666',
            access_token='test-token',
            # Saved the way Meta displays it, with the +.
            display_phone_number='+1 555 012 3456',
            display_name='Routing Shop',
            webhook_verify_token='routing-verify-token',
        )

    def test_matches_on_phone_number_id(self):
        found = _settings_for_metadata({
            'phone_number_id': '555000111222333',
            'display_phone_number': 'not-a-match',
        })
        self.assertEqual(found, self.setting)

    def test_falls_back_to_digits_of_display_number(self):
        """No phone_number_id on the payload, and the stored number has a +."""
        found = _settings_for_metadata({'display_phone_number': '15550123456'})
        self.assertEqual(found, self.setting)

    def test_unknown_number_returns_none(self):
        self.assertIsNone(_settings_for_metadata({
            'phone_number_id': '000', 'display_phone_number': '19998887777',
        }))
