"""Detect transfers between the user's own accounts so they don't count as income or spending."""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import date

MAX_DAYS_APART = 2
# Card payments can take a few business days to show on the card (e.g. Apple Card posts before Wells Fargo).
MAX_DAYS_APART_CARD = 4

_TRANSFER_WORDS = re.compile(r"\b(TRANSFER|XFER)\b", re.I)
_PAYMENT_WORDS = re.compile(r"\b(PAYMENT|PMT|PYMT|CRCARDPMT|AUTOPAY)\b", re.I)
# Debit card purchases: "RECURRING PAYMENT AUTHORIZED ON 07/05 APPLE.COM/BILL ..." is a purchase, not a bill payment.
_DEBIT_PURCHASE = re.compile(r"^\s*(PURCHASE|RECURRING PAYMENT)\b.*\bAUTHORIZED ON\b", re.I)
_ACCOUNT_MASK = re.compile(r"X{4,}(\d{4})\b", re.I)
_LAST4 = re.compile(r"(?<!\d)(\d{4})(?!\d)")


@dataclass(frozen=True)
class _Tx:
    account_id: str
    id: str
    day: date
    cents: int
    is_transfer: bool  # says TRANSFER/XFER; otherwise it only says PAYMENT/PMT...
    to_card: bool  # lands on a credit card account


def own_last4(conn: sqlite3.Connection) -> set[str]:
    """Last-4 digits of the user's accounts, taken from account names like 'CHECKING ...1234 (1234)'."""
    return {m for (name,) in conn.execute("SELECT name FROM accounts") for m in _LAST4.findall(name or "")}


def _names_only_own_accounts(description: str | None, own: set[str]) -> bool:
    # A transfer naming an account mask that isn't ours (e.g. "TO DOE J CHECKING XXXXXX9999") goes to someone else.
    masks = set(_ACCOUNT_MASK.findall(description or ""))
    return not masks or masks <= own


def is_transfer_candidate(payee: str | None, description: str | None, own: set[str]) -> bool:
    text = f"{payee or ''} {description or ''}"
    return bool(_TRANSFER_WORDS.search(text)) and _names_only_own_accounts(description, own)


def is_payment_candidate(payee: str | None, description: str | None, own: set[str]) -> bool:
    """A bill payment ("CAPITAL ONE MOBILE PMT", "APPLECARD GSBANK PAYMENT"); internal only if it lands on own card."""
    text = f"{payee or ''} {description or ''}"
    return (
        bool(_PAYMENT_WORDS.search(text))
        and not _DEBIT_PURCHASE.match(description or "")
        and _names_only_own_accounts(description, own)
    )


def _can_pair(out: _Tx, inc: _Tx) -> bool:
    if inc.account_id == out.account_id:
        return False
    if inc.to_card:  # paying one of the user's own credit cards
        return abs((inc.day - out.day).days) <= MAX_DAYS_APART_CARD
    return out.is_transfer and inc.is_transfer and abs((inc.day - out.day).days) <= MAX_DAYS_APART


def _pair_id(out: _Tx, inc: _Tx) -> str:
    return "tp-" + hashlib.sha1(f"{out.account_id}/{out.id}|{inc.account_id}/{inc.id}".encode()).hexdigest()[:12]


def match_internal_transfers(conn: sqlite3.Connection) -> int:
    """Recompute is_internal_transfer / transfer_pair_id for all transactions. Returns the number of pairs.

    A pair is money out of one own account and the same amount into a different own account within
    MAX_DAYS_APART days. Payments to an own credit card count too (both sides may say PAYMENT/PMT instead of
    TRANSFER) within MAX_DAYS_APART_CARD days. When several candidates fit, the closest dates win; each transaction
    pairs at most once.
    """
    own = own_last4(conn)
    outs: list[_Tx] = []
    ins: list[_Tx] = []
    for r in conn.execute(
        "SELECT t.account_id, t.id, t.date, t.amount, t.payee, t.description, a.kind "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id"
    ):
        is_transfer = is_transfer_candidate(r["payee"], r["description"], own)
        if not is_transfer and not is_payment_candidate(r["payee"], r["description"], own):
            continue
        cents = round(r["amount"] * 100)
        to_card = cents > 0 and r["kind"] == "credit"
        tx = _Tx(r["account_id"], r["id"], date.fromisoformat(r["date"]), cents, is_transfer, to_card)
        (outs if tx.cents < 0 else ins).append(tx)

    ins_by_cents: dict[int, list[_Tx]] = {}
    for tx in ins:
        ins_by_cents.setdefault(tx.cents, []).append(tx)

    candidates = [
        (abs((inc.day - out.day).days), out.day, out.id, inc.id, out, inc)
        for out in outs
        for inc in ins_by_cents.get(-out.cents, [])
        if _can_pair(out, inc)
    ]
    candidates.sort(key=lambda c: c[:4])

    used: set[tuple[str, str]] = set()
    updates: list[tuple[str, str, str]] = []
    for *_, out, inc in candidates:
        out_key, inc_key = (out.account_id, out.id), (inc.account_id, inc.id)
        if out_key in used or inc_key in used:
            continue
        used |= {out_key, inc_key}
        pair = _pair_id(out, inc)
        updates += [(pair, *out_key), (pair, *inc_key)]

    conn.execute("UPDATE transactions SET is_internal_transfer = 0, transfer_pair_id = NULL")
    conn.executemany(
        "UPDATE transactions SET is_internal_transfer = 1, transfer_pair_id = ? WHERE account_id = ? AND id = ?",
        updates,
    )
    return len(updates) // 2
