"""
M-Pesa Daraja API helpers – STK Push (Lipa Na M-Pesa Online).

Environment variables / Django settings required:
    MPESA_ENVIRONMENT   – "sandbox" or "production"
    MPESA_CONSUMER_KEY  – from Safaricom developer portal
    MPESA_CONSUMER_SECRET
    MPESA_SHORTCODE     – your Pay Bill / Till number (platform shortcode)
    MPESA_PASSKEY       – Lipa Na M-Pesa Online passkey
    MPESA_CALLBACK_URL  – publicly reachable URL for STK callback

The STK Push prompt is sent FROM the tenant's phone and goes TO the
landlord's configured Paybill / Till / personal number.  For sandbox
testing everything routes through the default test shortcode; in production
the payment lands directly in the landlord's M-Pesa.
"""

import base64
import logging
from datetime import datetime

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# ── URLs ────────────────────────────────────────────────────────────────────
_SANDBOX_BASE = "https://sandbox.safaricom.co.ke"
_PRODUCTION_BASE = "https://api.safaricom.co.ke"


def _base_url() -> str:
    env = getattr(settings, "MPESA_ENVIRONMENT", "sandbox").lower()
    return _PRODUCTION_BASE if env == "production" else _SANDBOX_BASE


# ── OAuth token ─────────────────────────────────────────────────────────────
def get_access_token() -> str:
    """Fetch a fresh OAuth access token from Safaricom."""
    url = f"{_base_url()}/oauth/v1/generate?grant_type=client_credentials"
    key = settings.MPESA_CONSUMER_KEY
    secret = settings.MPESA_CONSUMER_SECRET
    resp = requests.get(url, auth=(key, secret), timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── STK Push ────────────────────────────────────────────────────────────────
def _generate_password(shortcode: str, timestamp: str) -> str:
    """Base-64 encode  Shortcode + Passkey + Timestamp."""
    data = f"{shortcode}{settings.MPESA_PASSKEY}{timestamp}"
    return base64.b64encode(data.encode()).decode("utf-8")


def format_phone(phone: str) -> str:
    """Normalise a Kenyan phone number to 2547XXXXXXXX format."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    if phone.startswith("7") or phone.startswith("1"):
        phone = "254" + phone
    return phone


def initiate_stk_push(
    phone: str,
    amount: int,
    account_reference: str = "RentEasy",
    transaction_desc: str = "Rent Payment",
    *,
    landlord_payment_method=None,
) -> dict:
    """
    Send an STK Push request to Safaricom.

    If *landlord_payment_method* is provided the push will target the
    landlord's Paybill / Till / personal M-Pesa.  Otherwise the platform
    shortcode from settings is used (useful for sandbox testing).

    Returns the full JSON response from Daraja which includes:
        MerchantRequestID, CheckoutRequestID, ResponseCode,
        ResponseDescription, CustomerMessage
    """
    token = get_access_token()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    # Determine the receiving shortcode / number and transaction type.
    is_sandbox = getattr(settings, "MPESA_ENVIRONMENT", "sandbox").lower() == "sandbox"
    lpm = landlord_payment_method
    if lpm and lpm.method_type == "mpesa_paybill":
        shortcode = lpm.mpesa_number
        tx_type = "CustomerPayBillOnline"
        acct_ref = lpm.mpesa_account_number or account_reference
    elif lpm and lpm.method_type == "mpesa_till":
        shortcode = lpm.mpesa_number
        tx_type = "CustomerBuyGoodsOnline"
        acct_ref = account_reference
    elif lpm and lpm.method_type in ("mpesa_send_money", "mpesa_pochi"):
        # Send Money / Pochi: STK push still goes through the platform
        # shortcode and uses the landlord phone as the account reference.
        # An actual B2C disbursement would follow in production.
        shortcode = settings.MPESA_SHORTCODE
        tx_type = "CustomerPayBillOnline"
        acct_ref = lpm.mpesa_number  # landlord phone for reconciliation
    else:
        shortcode = settings.MPESA_SHORTCODE
        tx_type = "CustomerPayBillOnline"
        acct_ref = account_reference

    # In sandbox mode Safaricom only accepts the test shortcode 174379
    # with the corresponding passkey – override regardless of landlord config.
    if is_sandbox:
        shortcode = settings.MPESA_SHORTCODE

    password = _generate_password(shortcode, timestamp)

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": tx_type,
        "Amount": int(amount),
        "PartyA": format_phone(phone),
        "PartyB": shortcode,
        "PhoneNumber": format_phone(phone),
        "CallBackURL": settings.MPESA_CALLBACK_URL,
        "AccountReference": acct_ref,
        "TransactionDesc": transaction_desc,
    }

    url = f"{_base_url()}/mpesa/stkpush/v1/processrequest"
    headers = {"Authorization": f"Bearer {token}"}

    logger.info("STK Push → %s  phone=%s amount=%s dest=%s", url, format_phone(phone), amount, shortcode)
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    logger.info("STK Push response: %s", data)
    return data


# ── STK Query (poll Safaricom for transaction result) ───────────────────────
def query_stk_status(checkout_request_id: str) -> dict:
    """
    Ask Daraja for the result of a previously-sent STK Push.

    Returns the full JSON response which includes ResultCode:
        0  = success
        1032 = user cancelled
        1037 = timeout (user didn't respond)
        other = various failures
    """
    token = get_access_token()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    shortcode = settings.MPESA_SHORTCODE
    password = _generate_password(shortcode, timestamp)

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id,
    }

    url = f"{_base_url()}/mpesa/stkpushquery/v1/query"
    headers = {"Authorization": f"Bearer {token}"}

    logger.info("STK Query → %s  checkout=%s", url, checkout_request_id)
    resp = requests.post(url, json=payload, headers=headers, timeout=30)

    # Safaricom returns HTTP 500 while the transaction is still being
    # processed – that is NOT an error, just "no result yet".  Return the
    # body so the caller can inspect ResponseCode / ResultCode.
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()          # real server error, no JSON body
        data = {}

    logger.info("STK Query response (%s): %s", resp.status_code, data)
    return data
