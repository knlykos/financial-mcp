"""Detect transfers between the user's own accounts so they don't count as income or spending."""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import date

MAX_DAYS_APART = 2

_TRANSFER_WORDS = re.compile(r"\b(TRANSFER|XFER)\b", re.I)
_ACCOUNT_MASK = re.compile(r"X{4,}(\d{4})\b", re.I)
_LAST4 = re.compile(r"(?<!\d)(\d{4})(?!\d)")


@dataclass(frozen=True)
class _Tx:
    account_id: str
    id: str
    day: date
    cents: int


def own_last4(conn: sqlite3.Connection) -> set[str]:
    """Last-4 digits of the user's accounts, taken from account names like 'CHECKING ...1234 (1234)'."""
    return {m for (name,) in conn.execute("SELECT name FROM accounts") for m in _LAST4.findall(name or "")}


def is_transfer_candidate(payee: str | None, description: str | None, own: set[str]) -> bool:
    text = f"{payee or ''} {description or ''}"
    if not _TRANSFER_WORDS.search(text):
        return False
    # A transfer naming an account mask that isn't ours (e.g. "TO DOE J CHECKING XXXXXX9999") goes to someone else.
    masks = set(_ACCOUNT_MASK.findall(description or ""))
    return not masks or masks <= own


def _pair_id(out: _Tx, inc: _Tx) -> str:
    return "tp-" + hashlib.sha1(f"{out.account_id}/{out.id}|{inc.account_id}/{inc.id}".encode()).hexdigest()[:12]


def match_internal_transfers(conn: sqlite3.Connection) -> int:
    """Recompute is_internal_transfer / transfer_pair_id for all transactions. Returns the number of pairs.

    A pair is money out of one own account and the same amount into a different own account within
    MAX_DAYS_APART days. When several candidates fit, the closest dates win; each transaction pairs at most once.
    """
    own = own_last4(conn)
    outs: list[_Tx] = []
    ins: list[_Tx] = []
    for r in conn.execute(
        "SELECT t.account_id, t.id, t.date, t.amount, t.payee, t.description "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id"
    ):
        if not is_transfer_candidate(r["payee"], r["description"], own):
            continue
        tx = _Tx(r["account_id"], r["id"], date.fromisoformat(r["date"]), round(r["amount"] * 100))
        (outs if tx.cents < 0 else ins).append(tx)

    ins_by_cents: dict[int, list[_Tx]] = {}
    for tx in ins:
        ins_by_cents.setdefault(tx.cents, []).append(tx)

    candidates = [
        (abs((inc.day - out.day).days), out.day, out.id, inc.id, out, inc)
        for out in outs
        for inc in ins_by_cents.get(-out.cents, [])
        if inc.account_id != out.account_id and abs((inc.day - out.day).days) <= MAX_DAYS_APART
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
