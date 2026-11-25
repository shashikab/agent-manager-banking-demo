"""Mock banking data for one demo customer. Everything lives in memory and
resets on restart; nothing is persisted and nothing talks to a real bank."""

from __future__ import annotations

import itertools
import threading

# The customer's contact details on file (all fictional: example.com is a reserved
# domain and 555-01xx numbers are reserved for fiction). Deliberately holds no
# date of birth, government ID or credentials: the agent must never repeat those,
# so it should never have them.
CUSTOMER: dict = {
    "customer_id": "CUS-100245",
    "full_name": "Alex Morgan",
    "email": "alex.morgan@example.com",
    "phone": "+1 (415) 555-0142",
    "address": {
        "line1": "742 Maple Street, Apt 4B",
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94110",
        "country": "US",
    },
    "member_since": "2019-03-14",
}

CUSTOMER_NAME = CUSTOMER["full_name"]

# Guards every read-modify-write below: the /chat endpoint runs on a threadpool.
LOCK = threading.RLock()

# account_id -> account. Balances are in USD. The credit card balance is the
# amount owed, so it is stored as a negative number.
ACCOUNTS: dict[str, dict] = {
    "CHK-1001": {
        "account_id": "CHK-1001",
        "type": "checking",
        "nickname": "Everyday Checking",
        "number_last4": "4821",
        "balance": 2450.75,
        "currency": "USD",
        "transfers_allowed": True,
    },
    "SAV-2001": {
        "account_id": "SAV-2001",
        "type": "savings",
        "nickname": "Rainy Day Savings",
        "number_last4": "7390",
        "balance": 12800.00,
        "currency": "USD",
        "transfers_allowed": True,
    },
    "CC-3001": {
        "account_id": "CC-3001",
        "type": "credit card",
        "nickname": "Visa Platinum",
        "number_last4": "1156",
        "balance": -640.20,
        "currency": "USD",
        "credit_limit": 5000.00,
        # Moving money onto a credit card is a payment, which this demo agent
        # does not handle.
        "transfers_allowed": False,
    },
}

# account_id -> newest-first list of transactions.
TRANSACTIONS: dict[str, list[dict]] = {
    "CHK-1001": [
        {"date": "2026-11-10", "description": "Whole Foods Market", "amount": -86.42},
        {"date": "2026-11-09", "description": "Shell Gas Station", "amount": -52.10},
        {"date": "2026-11-07", "description": "Netflix", "amount": -15.49},
        {"date": "2026-11-05", "description": "Salary - Acme Corp", "amount": 3200.00},
        {"date": "2026-11-03", "description": "Rent - Maple Apartments", "amount": -1450.00},
        {"date": "2026-11-02", "description": "Blue Bottle Coffee", "amount": -6.75},
    ],
    "SAV-2001": [
        {"date": "2026-11-01", "description": "Interest payment", "amount": 31.20},
        {"date": "2026-10-15", "description": "Transfer from Everyday Checking", "amount": 500.00},
    ],
    "CC-3001": [
        {"date": "2026-11-08", "description": "Amazon", "amount": -129.99},
        {"date": "2026-11-06", "description": "Uber", "amount": -23.40},
        {"date": "2026-11-04", "description": "Spotify", "amount": -11.99},
    ],
}

# card_id -> card.
CARDS: dict[str, dict] = {
    "CARD-1": {
        "card_id": "CARD-1",
        "type": "debit",
        "linked_account": "CHK-1001",
        "number_last4": "9034",
        "status": "active",
    },
    "CARD-2": {
        "card_id": "CARD-2",
        "type": "credit",
        "linked_account": "CC-3001",
        "number_last4": "1156",
        "status": "active",
    },
}

# Static product information the agent may quote (rates are illustrative).
PRODUCT_RATES: dict[str, dict] = {
    "savings": {"product": "High-Yield Savings", "apy_percent": 4.10},
    "personal_loan": {"product": "Personal Loan", "apr_percent_from": 8.99, "term_months": "12-60"},
    "mortgage": {"product": "30-Year Fixed Mortgage", "apr_percent_from": 6.45},
    "credit_card": {"product": "Visa Platinum", "apr_percent": 19.99},
}

# Largest single transfer the agent may make between the customer's own accounts.
TRANSFER_LIMIT = 5000.00

_transfer_ids = itertools.count(1)


def next_transfer_id() -> str:
    return f"TRF-{next(_transfer_ids):05d}"
