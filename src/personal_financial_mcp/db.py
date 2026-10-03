"""Local SQLite store, so history accumulates beyond SimpleFIN's ~90-day window."""

import sqlite3
from datetime import date, datetime
from typing import Any

from .simplefin import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "data" / "finance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    institution       TEXT,
    currency          TEXT,
    balance           REAL,
    available_balance REAL,
    balance_date      TEXT,
    synced_at         TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    id          TEXT NOT NULL,
    date        TEXT NOT NULL,  -- transacted_at, else posted, else sync day (local date, YYYY-MM-DD)
    posted      TEXT,
    amount      REAL NOT NULL,  -- negative = money out, positive = money in
    description TEXT,
    payee       TEXT,
    memo        TEXT,
    pending     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, id)
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _local_date(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts).date().isoformat() if ts else None


def _num(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


def upsert_accounts(conn: sqlite3.Connection, accounts: list[dict[str, Any]]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        """
        INSERT INTO accounts (id, name, institution, currency, balance, available_balance, balance_date, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name, institution = excluded.institution, currency = excluded.currency,
            balance = excluded.balance, available_balance = excluded.available_balance,
            balance_date = excluded.balance_date, synced_at = excluded.synced_at
        """,
        [
            (
                a["id"],
                a["name"],
                a["org"].get("name") or a["org"].get("domain"),
                a.get("currency"),
                _num(a.get("balance")),
                _num(a.get("available-balance")),
                _local_date(a.get("balance-date")),
                now,
            )
            for a in accounts
        ],
    )


def upsert_transactions(conn: sqlite3.Connection, accounts: list[dict[str, Any]]) -> int:
    """Store transactions; returns how many were new."""
    # Pending transactions often change id once they post, so drop old pending rows and take the fresh set.
    conn.executemany("DELETE FROM transactions WHERE account_id = ? AND pending = 1", [(a["id"],) for a in accounts])
    before = conn.total_changes
    for a in accounts:
        conn.executemany(
            """
            INSERT INTO transactions (account_id, id, date, posted, amount, description, payee, memo, pending)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, id) DO UPDATE SET
                date = excluded.date, posted = excluded.posted, amount = excluded.amount,
                description = excluded.description, payee = excluded.payee, memo = excluded.memo,
                pending = excluded.pending
            """,
            [
                (
                    a["id"],
                    t["id"],
                    # Pending transactions may have posted = 0 and no transacted_at; treat them as today.
                    _local_date(t.get("transacted_at") or t.get("posted")) or date.today().isoformat(),
                    _local_date(t.get("posted")),
                    float(t["amount"]),
                    t.get("description"),
                    t.get("payee"),
                    t.get("memo") or None,
                    1 if t.get("pending") else 0,
                )
                for t in a.get("transactions", [])
            ],
        )
    return conn.total_changes - before


def query_transactions(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    account_ids: list[str] | None = None,
    search: str | None = None,
    include_pending: bool = True,
    limit: int = 500,
) -> list[dict[str, Any]]:
    sql = """
        SELECT t.date, t.amount, t.description, t.payee, t.memo, t.pending, a.name AS account, t.account_id, t.id
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE t.date BETWEEN ? AND ?
    """
    params: list[Any] = [start.isoformat(), end.isoformat()]
    if account_ids:
        sql += f" AND t.account_id IN ({','.join('?' * len(account_ids))})"
        params += account_ids
    if search:
        sql += " AND (t.description LIKE ? OR t.payee LIKE ? OR t.memo LIKE ?)"
        params += [f"%{search}%"] * 3
    if not include_pending:
        sql += " AND t.pending = 0"
    sql += " ORDER BY t.date DESC LIMIT ?"
    params.append(limit)
    return [dict(r) | {"pending": bool(r["pending"])} for r in conn.execute(sql, params)]
