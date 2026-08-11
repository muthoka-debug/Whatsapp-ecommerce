import base64
import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Safaricom's Daraja API can stall. Without an explicit timeout requests waits
# forever and the worker handling the payment is held open indefinitely.
DEFAULT_TIMEOUT = 30


class MpesaAPI:
    """M-Pesa API integration class"""
    
    def __init__(self, business=None):
        self.business = business
        self.environment = getattr(settings, 'MPESA_ENVIRONMENT', 'sandbox')
        
        # Set API endpoints based on environment
        if self.environment == 'production':
            self.base_url = 'https://api.safaricom.co.ke'
        else:
            self.base_url = 'https://sandbox.safaricom.co.ke'
        
        # Get credentials from business settings or global settings
        self.consumer_key = getattr(settings, 'MPESA_CONSUMER_KEY', '')
        self.consumer_secret = getattr(settings, 'MPESA_CONSUMER_SECRET', '')
        self.passkey = getattr(settings, 'MPESA_PASSKEY', '')
        self.shortcode = getattr(settings, 'MPESA_SHORTCODE', '174379')
        
        # If business has specific settings, override
        if business:
            try:
                from .models import MpesaSetting
                mpesa_settings = MpesaSetting.objects.get(business=business)
                if mpesa_settings.consumer_key:
                    self.consumer_key = mpesa_settings.consumer_key
                if mpesa_settings.consumer_secret:
                    self.consumer_secret = mpesa_settings.consumer_secret
                if mpesa_settings.passkey:
                    self.passkey = mpesa_settings.passkey
                if mpesa_settings.shortcode:
                    self.shortcode = mpesa_settings.shortcode
            except Exception:
                pass
        
        self.access_token = None
        self.token_expiry = 0
    
    def get_access_token(self):
        """Get OAuth access token"""
        # Check cache first
        cache_key = f"mpesa_token_{self.shortcode}"
        token = cache.get(cache_key)
        if token:
            return token
        
        try:
            # Encode credentials
            credentials = base64.b64encode(
                f"{self.consumer_key}:{self.consumer_secret}".encode()
            ).decode()
            
            headers = {
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/json'
            }
            
            url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
            response = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
            
            if response.status_code == 200:
                data = response.json()
                token = data.get('access_token')
                expires_in = data.get('expires_in', 3600)
                
                # Cache token for less than expiry time
                cache.set(cache_key, token, expires_in - 60)
                return token
            else:
                logger.error(f"Failed to get M-Pesa token: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"M-Pesa token error: {str(e)}")
            return None
    
    def stk_push(self, phone_number, amount, account_reference, transaction_desc=None):
        """
        Initiate STK Push (Lipa Na M-Pesa Online)
        
        Args:
            phone_number: Customer phone number (format: 2547XXXXXXXX)
            amount: Amount to charge
            account_reference: Order number or reference
            transaction_desc: Description of transaction
        
        Returns:
            dict: Response from M-Pesa API
        """
        token = self.get_access_token()
        if not token:
            return {'error': 'Failed to get access token'}
        
        # Format phone number
        if phone_number.startswith('0'):
            phone_number = '254' + phone_number[1:]
        elif phone_number.startswith('+'):
            phone_number = phone_number[1:]
        
        # Generate timestamp
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        
        # Generate password
        password_str = f"{self.shortcode}{self.passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()
        
        # Prepare payload
        payload = {
            'BusinessShortCode': self.shortcode,
            'Password': password,
            'Timestamp': timestamp,
            'TransactionType': 'CustomerPayBillOnline',
            'Amount': int(amount),
            'PartyA': phone_number,
            'PartyB': self.shortcode,
            'PhoneNumber': phone_number,
            'CallBackURL': getattr(settings, 'MPESA_CALLBACK_URL', 'https://yourdomain.com/api/v1/payments/mpesa/callback/'),
            'AccountReference': account_reference[:12],  # Max 12 characters
            'TransactionDesc': transaction_desc or 'Payment for order'
        }
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        url = f"{self.base_url}/mpesa/stkpush/v1/processrequest"
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"STK Push initiated: {result}")
                return result
            else:
                logger.error(f"STK Push failed: {response.text}")
                return {'error': response.text}
                
        except Exception as e:
            logger.error(f"STK Push error: {str(e)}")
            return {'error': str(e)}
    
    def query_status(self, checkout_request_id):
        """
        Query STK Push status
        
        Args:
            checkout_request_id: The checkout request ID from STK Push
        
        Returns:
            dict: Response from M-Pesa API
        """
        token = self.get_access_token()
        if not token:
            return {'error': 'Failed to get access token'}
        
        # Generate timestamp
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        
        # Generate password
        password_str = f"{self.shortcode}{self.passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()
        
        payload = {
            'BusinessShortCode': self.shortcode,
            'Password': password,
            'Timestamp': timestamp,
            'CheckoutRequestID': checkout_request_id
        }
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        url = f"{self.base_url}/mpesa/stkpushquery/v1/query"
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"STK Query result: {result}")
                return result
            else:
                logger.error(f"STK Query failed: {response.text}")
                return {'error': response.text}
                
        except Exception as e:
            logger.error(f"STK Query error: {str(e)}")
            return {'error': str(e)}
    
    def b2c_payment(self, phone_number, amount, command_id='BusinessPayment', remarks=''):
        """
        Initiate B2C (Business to Customer) payment
        
        Args:
            phone_number: Recipient phone number
            amount: Amount to send
            command_id: BusinessPayment, SalaryPayment, PromotionPayment
            remarks: Payment remarks
        
        Returns:
            dict: Response from M-Pesa API
        """
        token = self.get_access_token()
        if not token:
            return {'error': 'Failed to get access token'}
        
        # Format phone number
        if phone_number.startswith('0'):
            phone_number = '254' + phone_number[1:]
        elif phone_number.startswith('+'):
            phone_number = phone_number[1:]
        
        # Generate timestamp
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        
        # Generate password
        password_str = f"{self.shortcode}{self.passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()
        
        payload = {
            'InitiatorName': getattr(settings, 'MPESA_INITIATOR_NAME', ''),
            'SecurityCredential': getattr(settings, 'MPESA_SECURITY_CREDENTIAL', ''),
            'CommandID': command_id,
            'Amount': int(amount),
            'PartyA': self.shortcode,
            'PartyB': phone_number,
            'Remarks': remarks[:100],
            'QueueTimeOutURL': getattr(settings, 'MPESA_QUEUE_TIMEOUT_URL', ''),
            'ResultURL': getattr(settings, 'MPESA_RESULT_URL', ''),
            'Occasion': 'Payment'
        }
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        url = f"{self.base_url}/mpesa/b2c/v3/paymentrequest"
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"B2C initiated: {result}")
                return result
            else:
                logger.error(f"B2C failed: {response.text}")
                return {'error': response.text}
                
        except Exception as e:
            logger.error(f"B2C error: {str(e)}")
            return {'error': str(e)}
    
    def register_c2b_urls(self, confirmation_url=None, validation_url=None):
        """
        Register C2B (Customer to Business) URLs
        
        Args:
            confirmation_url: URL for confirmation
            validation_url: URL for validation
        
        Returns:
            dict: Response from M-Pesa API
        """
        token = self.get_access_token()
        if not token:
            return {'error': 'Failed to get access token'}
        
        payload = {
            'ShortCode': self.shortcode,
            'ResponseType': 'Completed',
            'ConfirmationURL': confirmation_url or getattr(settings, 'MPESA_CONFIRMATION_URL', ''),
            'ValidationURL': validation_url or getattr(settings, 'MPESA_VALIDATION_URL', '')
        }
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        url = f"{self.base_url}/mpesa/c2b/v1/registerurl"
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"C2B URLs registered: {result}")
                return result
            else:
                logger.error(f"C2B registration failed: {response.text}")
                return {'error': response.text}
                
        except Exception as e:
            logger.error(f"C2B registration error: {str(e)}")
            return {'error': str(e)}