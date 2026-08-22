"""Connect a business to the WhatsApp Business Cloud API.

Creates or updates the business's ``WhatsAppSetting`` from credentials taken
from the environment (or flags), checks them against Graph, and prints the two
values Meta asks for when you subscribe the webhook.

Credentials are read from the environment by default so the access token never
lands in shell history or in ``ps`` output:

    WHATSAPP_PHONE_NUMBER_ID=...
    WHATSAPP_BUSINESS_ACCOUNT_ID=...
    WHATSAPP_ACCESS_TOKEN=...
    WHATSAPP_DISPLAY_PHONE_NUMBER=+254...
    WHATSAPP_DISPLAY_NAME=Nimo
    WHATSAPP_WEBHOOK_BASE_URL=https://<tunnel>.ngrok-free.app

Then:

    python manage.py setup_whatsapp --business Nimo
"""

import secrets

import requests
from decouple import config
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from businesses.models import Business
from whatsapp_integration.client import WhatsAppAPIError, WhatsAppClient
from whatsapp_integration.models import WhatsAppSetting

WEBHOOK_PATH = '/api/v1/whatsapp/webhook/'


class Command(BaseCommand):
    help = 'Create or update the WhatsApp Cloud API connection for a business.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--business',
            help='Business name or id. Optional when only one business exists.',
        )
        parser.add_argument('--phone-number-id', default=None)
        parser.add_argument('--business-account-id', default=None)
        parser.add_argument(
            '--access-token',
            default=None,
            help='Prefer the WHATSAPP_ACCESS_TOKEN env var; a token passed here '
                 'is visible to other processes on the machine.',
        )
        parser.add_argument('--display-phone-number', default=None)
        parser.add_argument('--display-name', default=None)
        parser.add_argument(
            '--webhook-base-url',
            default=None,
            help=f'Public https origin; {WEBHOOK_PATH} is appended.',
        )
        parser.add_argument(
            '--verify-token',
            default=None,
            help='Reuses the existing one, or generates one, when omitted.',
        )
        parser.add_argument(
            '--skip-check',
            action='store_true',
            help='Skip the Graph API credential check (offline setup).',
        )
        parser.add_argument(
            '--test-send',
            metavar='MSISDN',
            help='After saving, send a text to this number to prove the '
                 'connection. Only works inside the 24-hour customer service '
                 'window — the number must have messaged you first.',
        )

    def handle(self, *args, **options):
        business = self._resolve_business(options.get('business'))
        existing = WhatsAppSetting.objects.filter(business=business).first()

        phone_number_id = self._require(
            options['phone_number_id'], 'WHATSAPP_PHONE_NUMBER_ID',
            existing and existing.phone_number_id, 'phone number id',
        )
        business_account_id = self._require(
            options['business_account_id'], 'WHATSAPP_BUSINESS_ACCOUNT_ID',
            existing and existing.business_account_id, 'business account id',
        )
        access_token = self._require(
            options['access_token'], 'WHATSAPP_ACCESS_TOKEN',
            existing and existing.access_token, 'access token',
        )
        display_phone_number = self._require(
            options['display_phone_number'], 'WHATSAPP_DISPLAY_PHONE_NUMBER',
            existing and existing.display_phone_number, 'display phone number',
        )
        display_name = (
            options['display_name']
            or config('WHATSAPP_DISPLAY_NAME', default='')
            or (existing and existing.display_name)
            or business.name
        )
        base_url = (
            options['webhook_base_url']
            or config('WHATSAPP_WEBHOOK_BASE_URL', default='')
            or (existing and existing.webhook_url.replace(WEBHOOK_PATH, ''))
            or ''
        )
        webhook_url = f"{base_url.rstrip('/')}{WEBHOOK_PATH}" if base_url else ''

        # The verify token is what Meta echoes back during the handshake, and it
        # is how the webhook view works out which business is subscribing — so
        # it must survive re-runs rather than being regenerated each time.
        verify_token = (
            options['verify_token']
            or (existing and existing.webhook_verify_token)
            or secrets.token_urlsafe(24)
        )

        if not options['skip_check']:
            self._check_credentials(phone_number_id, access_token)

        setting, created = WhatsAppSetting.objects.update_or_create(
            business=business,
            defaults={
                'phone_number_id': phone_number_id,
                'business_account_id': business_account_id,
                'access_token': access_token,
                'display_phone_number': display_phone_number,
                'display_name': display_name,
                'webhook_url': webhook_url,
                'webhook_verify_token': verify_token,
                'is_active': True,
            },
        )

        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} WhatsApp settings for {business.name}"
        ))
        self.stdout.write('')
        self.stdout.write('Configure in Meta > WhatsApp > Configuration:')
        self.stdout.write(f'  Callback URL:  {webhook_url or "(set --webhook-base-url)"}')
        self.stdout.write(f'  Verify token:  {verify_token}')
        self.stdout.write(
            '  Subscribe to the "messages" field, then send a message to '
            f'{display_phone_number} to confirm delivery.'
        )

        if not getattr(settings, 'WHATSAPP_APP_SECRET', ''):
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'WHATSAPP_APP_SECRET is unset. Inbound webhooks are accepted '
                'unverified while DEBUG=True and rejected outright once it is '
                'off — set it in .env before deploying.'
            ))

        if options['test_send']:
            self._test_send(setting, options['test_send'])

    def _resolve_business(self, ref):
        if ref:
            business = (
                Business.objects.filter(name__iexact=ref).first()
                or Business.objects.filter(pk=ref).first()
            )
            if not business:
                raise CommandError(f'No business matching {ref!r}')
            return business

        businesses = list(Business.objects.all()[:2])
        if len(businesses) == 1:
            return businesses[0]
        if not businesses:
            raise CommandError('No businesses exist yet — create one first.')
        raise CommandError('Several businesses exist; pass --business.')

    def _require(self, flag_value, env_name, existing_value, label):
        value = flag_value or config(env_name, default='') or existing_value
        if not value:
            raise CommandError(f'Missing {label}: pass --{label.replace(" ", "-")} '
                               f'or set {env_name}.')
        return value

    def _check_credentials(self, phone_number_id, access_token):
        """Fail here, with Meta's own error, rather than at the first send."""
        base = getattr(settings, 'WHATSAPP_API_BASE_URL', 'https://graph.facebook.com')
        version = getattr(settings, 'WHATSAPP_API_VERSION', 'v21.0')
        url = f"{base.rstrip('/')}/{version}/{phone_number_id}"

        try:
            response = requests.get(
                url,
                params={'fields': 'display_phone_number,verified_name,quality_rating'},
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise CommandError(f'Could not reach the WhatsApp API: {exc}') from exc

        if response.status_code >= 400:
            raise CommandError(
                f'WhatsApp rejected the credentials ({response.status_code}): '
                f'{response.text}'
            )

        data = response.json()
        self.stdout.write(self.style.SUCCESS(
            f"Credentials OK — {data.get('verified_name', '?')} "
            f"({data.get('display_phone_number', '?')}), "
            f"quality {data.get('quality_rating', '?')}"
        ))

    def _test_send(self, setting, to):
        try:
            message_id = WhatsAppClient(setting).send_text(
                to, 'WhatsApp connection test — you can ignore this message.'
            )
        except WhatsAppAPIError as exc:
            raise CommandError(f'Test send failed: {exc}') from exc

        self.stdout.write(self.style.SUCCESS(f'Test message sent: {message_id}'))
