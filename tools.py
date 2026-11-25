"""Tools exposed to the agent. Strands derives each tool's schema from the
function signature and the Args section of its docstring.

Every tool returns a JSON string. Rules the model must not be trusted with
(balances, limits, which accounts can move money) are enforced here, not just
described in the prompt.
"""

from __future__ import annotations

import json
import math
from datetime import date

from strands import tool

from data import (
    ACCOUNTS,
    CARDS,
    CUSTOMER,
    CUSTOMER_NAME,
    LOCK,
    PRODUCT_RATES,
    TRANSACTIONS,
    TRANSFER_LIMIT,
    next_transfer_id,
)


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _account(account_id: str) -> dict | None:
    return ACCOUNTS.get(account_id.strip().upper())


def _public_account(acct: dict) -> dict:
    """Account view for the model: last four digits only, never a full number."""
    view = {
        "account_id": acct["account_id"],
        "type": acct["type"],
        "nickname": acct["nickname"],
        "ending_in": acct["number_last4"],
        "balance": acct["balance"],
        "currency": acct["currency"],
    }
    if "credit_limit" in acct:
        view["credit_limit"] = acct["credit_limit"]
        view["available_credit"] = round(acct["credit_limit"] + acct["balance"], 2)
    return view


@tool
def list_accounts() -> str:
    """List all of the customer's accounts with their current balances.

    Use this whenever the customer asks about "my accounts" or "my balance".
    A negative balance on a credit card is the amount owed.
    """
    with LOCK:
        return json.dumps(
            {
                "customer": CUSTOMER_NAME,
                "accounts": [_public_account(a) for a in ACCOUNTS.values()],
            }
        )


@tool
def get_customer_profile() -> str:
    """Get the customer's own details on file: name, email, phone number, mailing
    address, customer ID and the date they joined the bank.

    Use this when the customer asks what you have on file for them, or asks you to
    repeat or confirm their name, email, phone number or address.
    """
    with LOCK:
        addr = CUSTOMER["address"]
        return json.dumps(
            {
                "customer_id": CUSTOMER["customer_id"],
                "name": CUSTOMER["full_name"],
                "email": CUSTOMER["email"],
                "phone": CUSTOMER["phone"],
                "mailing_address": (
                    f"{addr['line1']}, {addr['city']}, {addr['state']} "
                    f"{addr['postal_code']}, {addr['country']}"
                ),
                "member_since": CUSTOMER["member_since"],
            }
        )


@tool
def get_recent_transactions(account_id: str, limit: int = 5) -> str:
    """Get the most recent transactions on one account, newest first.

    Args:
        account_id: Account ID from list_accounts, e.g. CHK-1001.
        limit: How many transactions to return (1-10). Defaults to 5.
    """
    with LOCK:
        acct = _account(account_id)
        if acct is None:
            return _error(f"No account found with ID {account_id}.")
        limit = max(1, min(int(limit), 10))
        return json.dumps(
            {
                "account_id": acct["account_id"],
                "nickname": acct["nickname"],
                "transactions": TRANSACTIONS[acct["account_id"]][:limit],
            }
        )


@tool
def transfer_between_accounts(from_account: str, to_account: str, amount: float) -> str:
    """Move money between two of the customer's own checking or savings accounts.

    Only call this after the customer has explicitly confirmed the source
    account, the destination account and the amount in this conversation.

    Args:
        from_account: Account ID to take the money from, e.g. CHK-1001.
        to_account: Account ID to put the money into, e.g. SAV-2001.
        amount: Amount in US dollars, greater than zero.
    """
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return _error("Amount must be a number.")
    if not math.isfinite(amount) or amount <= 0:
        return _error("Amount must be greater than zero.")
    amount = round(amount, 2)
    if amount > TRANSFER_LIMIT:
        return _error(
            f"Transfers are limited to ${TRANSFER_LIMIT:,.2f} each. "
            "For a larger amount the customer must contact a branch."
        )

    with LOCK:
        src, dst = _account(from_account), _account(to_account)
        if src is None:
            return _error(f"No account found with ID {from_account}.")
        if dst is None:
            return _error(f"No account found with ID {to_account}.")
        if src["account_id"] == dst["account_id"]:
            return _error("The source and destination accounts must be different.")
        for acct in (src, dst):
            if not acct["transfers_allowed"]:
                return _error(
                    f"{acct['nickname']} cannot be used for transfers. "
                    "Credit card payments are not supported by this assistant."
                )
        if src["balance"] < amount:
            return _error(
                f"Insufficient funds: {src['nickname']} has ${src['balance']:,.2f} available."
            )

        src["balance"] = round(src["balance"] - amount, 2)
        dst["balance"] = round(dst["balance"] + amount, 2)
        today = date.today().isoformat()
        TRANSACTIONS[src["account_id"]].insert(
            0,
            {"date": today, "description": f"Transfer to {dst['nickname']}", "amount": -amount},
        )
        TRANSACTIONS[dst["account_id"]].insert(
            0,
            {"date": today, "description": f"Transfer from {src['nickname']}", "amount": amount},
        )
        return json.dumps(
            {
                "status": "completed",
                "transfer_id": next_transfer_id(),
                "amount": amount,
                "from": {"account_id": src["account_id"], "new_balance": src["balance"]},
                "to": {"account_id": dst["account_id"], "new_balance": dst["balance"]},
            }
        )


@tool
def list_cards() -> str:
    """List the customer's debit and credit cards and whether each is active or blocked."""
    with LOCK:
        return json.dumps(
            {
                "cards": [
                    {
                        "card_id": c["card_id"],
                        "type": c["type"],
                        "ending_in": c["number_last4"],
                        "linked_account": c["linked_account"],
                        "status": c["status"],
                    }
                    for c in CARDS.values()
                ]
            }
        )


@tool
def block_card(card_id: str) -> str:
    """Immediately block a card that is lost, stolen or suspected of fraud.

    Blocking stops all new purchases and withdrawals. Only call this after the
    customer has said which card and confirmed they want it blocked.

    Args:
        card_id: Card ID from list_cards, e.g. CARD-1.
    """
    with LOCK:
        card = CARDS.get(card_id.strip().upper())
        if card is None:
            return _error(f"No card found with ID {card_id}.")
        if card["status"] == "blocked":
            return json.dumps({"card_id": card["card_id"], "status": "blocked", "note": "Already blocked."})
        card["status"] = "blocked"
        return json.dumps(
            {
                "card_id": card["card_id"],
                "ending_in": card["number_last4"],
                "status": "blocked",
                "note": "To get a replacement card, the customer should contact the bank.",
            }
        )


@tool
def get_product_rates(product: str = "all") -> str:
    """Get current interest rates for the bank's products.

    Args:
        product: One of savings, personal_loan, mortgage, credit_card, or all.
    """
    key = product.strip().lower()
    if key == "all":
        return json.dumps({"rates": list(PRODUCT_RATES.values())})
    rate = PRODUCT_RATES.get(key)
    if rate is None:
        return _error(
            f"Unknown product '{product}'. Choose from: {', '.join(PRODUCT_RATES)}."
        )
    return json.dumps(rate)


ALL_TOOLS = [
    list_accounts,
    get_customer_profile,
    get_recent_transactions,
    transfer_between_accounts,
    list_cards,
    block_card,
    get_product_rates,
]
