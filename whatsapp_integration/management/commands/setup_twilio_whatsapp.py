"""Connect a business to WhatsApp through Twilio.

The Twilio counterpart of ``setup_whatsapp``. Twilio's sandbox hands out a
working WhatsApp number immediately, with no Meta app review, which makes this
the quickest way to get a live number attached to a business.

Credentials are read from the environment by default so the auth token never
lands in shell history or in ``ps`` output:

    TWILIO_ACCOUNT_SID=AC...
    TWILIO_AUTH_TOKEN=...
    TWILIO_WHATSAPP_NUMBER=+14155238886
    WHATSAPP_WEBHOOK_BASE_URL=https://<tunnel>.ngrok-free.app

Then:

    python manage.py setup_twilio_whatsapp --business Nimo
"""

import secrets

import requests
from decouple import config
from django.core.management.base import BaseCommand, CommandError

from businesses.models import Business
from whatsapp_integration.client import WhatsAppAPIError
from whatsapp_integration.models import WhatsAppSetting
from whatsapp_integration.twilio_client import (
    API_BASE_URL,
    TwilioWhatsAppClient,
    WHATSAPP_PREFIX,
    to_whatsapp_address,
)

WEBHOOK_PATH = '/api/v1/whatsapp/webhook/twilio/'

#: Twilio's shared sandbox sender, used by every account until a number of your
#: own is approved.
SANDBOX_NUMBER = '+14155238886'


class Command(BaseCommand):
    help = 'Create or update the Twilio WhatsApp connection for a business.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--business',
            help='Business name or id. Optional when only one business exists.',
        )
        parser.add_argument('--account-sid', default=None)
        parser.add_argument(
            '--auth-token',
            default=None,
            help='Prefer the TWILIO_AUTH_TOKEN env var; a token passed here is '
                 'visible to other processes on the machine.',
        )
        parser.add_argument(
            '--whatsapp-number',
            default=None,
            help=f'Twilio WhatsApp sender. Defaults to the sandbox, {SANDBOX_NUMBER}.',
        )
        parser.add_argument('--display-name', default=None)
        parser.add_argument(
            '--webhook-base-url',
            default=None,
            help=f'Public https origin; {WEBHOOK_PATH} is appended.',
        )
        parser.add_argument(
            '--skip-check',
            action='store_true',
            help='Skip the Twilio credential check (offline setup).',
        )
        parser.add_argument(
            '--test-send',
            metavar='MSISDN',
            help='After saving, send a text to this number to prove the '
                 'connection. On the sandbox the number must have joined it '
                 'first, and in general it must have messaged you within 24 '
                 'hours.',
        )

    def handle(self, *args, **options):
        business = self._resolve_business(options.get('business'))
        existing = WhatsAppSetting.objects.filter(business=business).first()
        # Meta credentials in an existing row must not be offered as defaults
        # for Twilio fields — they are different secrets entirely.
        reusable = existing if existing and existing.provider == 'TWILIO' else None

        account_sid = self._require(
            options['account_sid'], 'TWILIO_ACCOUNT_SID',
            reusable and reusable.business_account_id, 'account sid',
        )
        auth_token = self._require(
            options['auth_token'], 'TWILIO_AUTH_TOKEN',
            reusable and reusable.access_token, 'auth token',
        )
        number = (
            options['whatsapp_number']
            or config('TWILIO_WHATSAPP_NUMBER', default='')
            or config('TWILIO_PHONE_NUMBER', default='')
            or (reusable and reusable.display_phone_number)
            or SANDBOX_NUMBER
        )
        # Stored without the whatsapp: scheme so the number compares against
        # what the webhook receives; the client adds the prefix when sending.
        number = to_whatsapp_address(number)[len(WHATSAPP_PREFIX):]

        display_name = (
            options['display_name']
            or config('WHATSAPP_DISPLAY_NAME', default='')
            or (existing and existing.display_name)
            or business.name
        )
        base_url = (
            options['webhook_base_url']
            or config('WHATSAPP_WEBHOOK_BASE_URL', default='')
            or ''
        )
        webhook_url = f"{base_url.rstrip('/')}{WEBHOOK_PATH}" if base_url else ''

        # Twilio never uses a verify token — it authenticates with
        # X-Twilio-Signature — but the column is unique and NOT NULL, so the row
        # still needs one.
        verify_token = (
            existing and existing.webhook_verify_token
        ) or secrets.token_urlsafe(24)

        if not options['skip_check']:
            self._check_credentials(account_sid, auth_token)

        if existing and existing.provider == 'META':
            self.stdout.write(self.style.WARNING(
                f'{business.name} was connected to the Meta Cloud API; '
                'switching it to Twilio. Its Meta credentials are overwritten.'
            ))

        setting, created = WhatsAppSetting.objects.update_or_create(
            business=business,
            defaults={
                'provider': 'TWILIO',
                'phone_number_id': number,
                'business_account_id': account_sid,
                'access_token': auth_token,
                'display_phone_number': number,
                'display_name': display_name,
                'webhook_url': webhook_url,
                'webhook_verify_token': verify_token,
                'is_active': True,
            },
        )

        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} Twilio WhatsApp settings "
            f'for {business.name}'
        ))
        self.stdout.write('')

        if number == SANDBOX_NUMBER:
            self.stdout.write(
                'Sandbox sender in use. Each phone that wants to message you '
                'must first send the join phrase (Twilio Console > Messaging > '
                f'Try it out > WhatsApp) to {SANDBOX_NUMBER}.'
            )
            self.stdout.write('')

        self.stdout.write('Configure in the Twilio Console, under the WhatsApp')
        self.stdout.write("sandbox settings (or your sender's messaging config):")
        self.stdout.write(
            f'  When a message comes in:  {webhook_url or "(set --webhook-base-url)"}'
        )
        self.stdout.write(
            f'  Status callback URL:      {webhook_url or "(set --webhook-base-url)"}'
        )
        self.stdout.write('  Method: HTTP POST for both.')

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

    def _check_credentials(self, account_sid, auth_token):
        """Fail here, with Twilio's own error, rather than at the first send."""
        url = f'{API_BASE_URL}/Accounts/{account_sid}.json'

        try:
            response = requests.get(url, auth=(account_sid, auth_token), timeout=15)
        except requests.RequestException as exc:
            raise CommandError(f'Could not reach the Twilio API: {exc}') from exc

        if response.status_code == 401:
            raise CommandError(
                'Twilio rejected those credentials. Check the Account SID and '
                'Auth Token on the Twilio Console dashboard.'
            )
        if response.status_code >= 400:
            raise CommandError(
                f'Twilio returned {response.status_code}: {response.text}'
            )

        account = response.json()
        self.stdout.write(
            f"Authenticated as {account.get('friendly_name') or account_sid} "
            f"(status: {account.get('status', 'unknown')})"
        )

    def _test_send(self, setting, recipient):
        self.stdout.write('')
        try:
            message_id = TwilioWhatsAppClient(setting).send_text(
                recipient, f'{setting.display_name} is connected to WhatsApp.'
            )
        except WhatsAppAPIError as exc:
            raise CommandError(
                f'Test send failed: {exc}\n'
                'On the sandbox, the recipient must have sent the join phrase '
                'first. Otherwise they must have messaged you in the last 24 '
                'hours.'
            ) from exc

        self.stdout.write(self.style.SUCCESS(
            f'Test message sent to {recipient} (sid {message_id}).'
        ))
