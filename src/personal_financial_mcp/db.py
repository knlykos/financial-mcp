"""Local SQLite store, so history accumulates beyond SimpleFIN's ~90-day window."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .normalize import SEED_ALIASES, Alias, compile_aliases, normalize_payee
from .simplefin import PROJECT_ROOT
from . import categories
from .transfers import match_internal_transfers

DB_PATH = PROJECT_ROOT / "data" / "finance.db"
# Personal aliases/rules (employer, own name...) that must stay out of git.
LOCAL_RULES_FILE = PROJECT_ROOT / "local_rules.json"


# --- Migrations ---------------------------------------------------------------------------------
# Each migration runs once, in order; PRAGMA user_version stores how many have been applied.
# They must stay idempotent so a half-migrated or legacy (pre-versioning) database still upgrades.


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _m1_base(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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
    )


def _m2_payee_normalized(conn: sqlite3.Connection) -> None:
    _add_column(conn, "transactions", "payee_normalized", "TEXT NOT NULL DEFAULT ''")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS payee_aliases (
            id         INTEGER PRIMARY KEY,
            pattern    TEXT NOT NULL UNIQUE,  -- case-insensitive regex
            normalized TEXT NOT NULL,
            field      TEXT NOT NULL DEFAULT 'payee',  -- payee (description if no payee) | description
            priority   INTEGER NOT NULL DEFAULT 100,  -- lower wins
            source     TEXT NOT NULL DEFAULT 'user'   -- seed | local | user
        );
        CREATE INDEX IF NOT EXISTS idx_transactions_payee_normalized ON transactions(payee_normalized);
        """
    )
    # Backfill happens in _sync_aliases, which runs right after migrations.


def _m3_internal_transfers(conn: sqlite3.Connection) -> None:
    _add_column(conn, "transactions", "is_internal_transfer", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "transactions", "transfer_pair_id", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_pair ON transactions(transfer_pair_id)")
    match_internal_transfers(conn)


def _m4_categories(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id     INTEGER PRIMARY KEY,
            name   TEXT NOT NULL UNIQUE,
            parent TEXT,
            kind   TEXT NOT NULL CHECK (kind IN ('income', 'expense', 'transfer', 'debt')),
            source TEXT NOT NULL DEFAULT 'user'  -- seed | local | user
        );

        CREATE TABLE IF NOT EXISTS category_rules (
            id          INTEGER PRIMARY KEY,
            match_field TEXT NOT NULL,  -- payee_normalized | payee | description | memo
            match_type  TEXT NOT NULL CHECK (match_type IN ('exact', 'contains', 'regex')),
            pattern     TEXT NOT NULL,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            direction   TEXT NOT NULL DEFAULT 'any' CHECK (direction IN ('any', 'in', 'out')),
            priority    INTEGER NOT NULL DEFAULT 10,  -- lower wins
            source      TEXT NOT NULL DEFAULT 'user',  -- seed | local | user
            created_at  TEXT NOT NULL
        );
        """
    )
    _add_column(conn, "transactions", "category_id", "INTEGER REFERENCES categories(id)")
    _add_column(conn, "transactions", "category_source", "TEXT NOT NULL DEFAULT 'none'")  # rule | manual | none
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id)")
    # Seeding and the first categorization happen in _refresh_derived, right after migrations.


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _m1_base, _m2_payee_normalized, _m3_internal_transfers, _m4_categories,
]


def _backup(conn: sqlite3.Connection, version: int) -> Path:
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    path = backup_dir / f"finance-{datetime.now():%Y%m%d-%H%M%S}-v{version}.db"
    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return path


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= len(MIGRATIONS):
        return
    has_data = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1").fetchone()
    if has_data:
        _backup(conn, version)
    for i, migration in enumerate(MIGRATIONS[version:], start=version + 1):
        migration(conn)
        conn.execute(f"PRAGMA user_version = {i}")
        conn.commit()


# --- Aliases --------------------------------------------------------------------------------------


def _load_local_rules() -> dict[str, Any]:
    if not LOCAL_RULES_FILE.exists():
        return {}
    return json.loads(LOCAL_RULES_FILE.read_text(encoding="utf-8"))


def _sync_aliases(conn: sqlite3.Connection) -> None:
    """Make the seed and local aliases in the DB match the code and local_rules.json; renormalize if they changed."""
    wanted = [(p, n, "payee", 100, "seed") for p, n in SEED_ALIASES]
    wanted += [
        (a["pattern"], a["normalized"], a.get("field", "payee"), 50, "local")
        for a in _load_local_rules().get("payee_aliases", [])
    ]

    snapshot = lambda: conn.execute(
        "SELECT pattern, normalized, field, priority, source FROM payee_aliases ORDER BY id"
    ).fetchall()
    before = [tuple(r) for r in snapshot()]
    managed = [w[0] for w in wanted]
    conn.execute(
        f"DELETE FROM payee_aliases WHERE source IN ('seed', 'local') AND pattern NOT IN ({','.join('?' * len(managed))})",
        managed,
    )
    conn.executemany(
        """
        INSERT INTO payee_aliases (pattern, normalized, field, priority, source) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(pattern) DO UPDATE SET normalized = excluded.normalized, field = excluded.field,
                                           priority = excluded.priority, source = excluded.source
        """,
        wanted,
    )
    stale = conn.execute("SELECT 1 FROM transactions WHERE payee_normalized = '' LIMIT 1").fetchone()
    if [tuple(r) for r in snapshot()] != before or stale:
        renormalize_all(conn)


def load_aliases(conn: sqlite3.Connection) -> list[Alias]:
    rows = conn.execute("SELECT pattern, normalized, field FROM payee_aliases ORDER BY priority, id").fetchall()
    return compile_aliases([(r["pattern"], r["normalized"], r["field"]) for r in rows])


def renormalize_all(conn: sqlite3.Connection) -> int:
    aliases = load_aliases(conn)
    rows = conn.execute("SELECT account_id, id, payee, description FROM transactions").fetchall()
    conn.executemany(
        "UPDATE transactions SET payee_normalized = ? WHERE account_id = ? AND id = ?",
        [(normalize_payee(r["payee"], r["description"], aliases), r["account_id"], r["id"]) for r in rows],
    )
    return len(rows)


def _refresh_derived(conn: sqlite3.Connection) -> None:
    """Recompute everything derived from raw transactions, once per process.

    Cheap at personal-finance scale, and it repairs rows written by an older version of the server.
    """
    _sync_aliases(conn)
    match_internal_transfers(conn)
    categories.sync_seed_rules(conn, _load_local_rules())
    categories.recategorize(conn)


# --- Connection -----------------------------------------------------------------------------------

_initialized: set[Path] = set()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open the DB (migrating it on first use in this process), commit on success, always close."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if DB_PATH not in _initialized:
            _migrate(conn)
            _refresh_derived(conn)
            conn.commit()
            _initialized.add(DB_PATH)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- Writes ---------------------------------------------------------------------------------------


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
    """Store transactions; returns how many rows were written."""
    aliases = load_aliases(conn)
    # Pending transactions often change id once they post, so drop old pending rows and take the fresh set.
    conn.executemany("DELETE FROM transactions WHERE account_id = ? AND pending = 1", [(a["id"],) for a in accounts])
    before = conn.total_changes
    for a in accounts:
        conn.executemany(
            """
            INSERT INTO transactions (account_id, id, date, posted, amount, description, payee, memo, pending,
                                      payee_normalized)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, id) DO UPDATE SET
                date = excluded.date, posted = excluded.posted, amount = excluded.amount,
                description = excluded.description, payee = excluded.payee, memo = excluded.memo,
                pending = excluded.pending, payee_normalized = excluded.payee_normalized
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
                    normalize_payee(t.get("payee"), t.get("description"), aliases),
                )
                for t in a.get("transactions", [])
            ],
        )
    return conn.total_changes - before


# --- Reads ----------------------------------------------------------------------------------------


def query_transactions(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    account_ids: list[str] | None = None,
    search: str | None = None,
    include_pending: bool = True,
    limit: int = 500,
    exclude_internal: bool = False,
    category_id: int | None = None,
    uncategorized: bool = False,
) -> list[dict[str, Any]]:
    sql = """
        SELECT t.date, t.amount, t.description, t.payee, t.payee_normalized, t.memo, t.pending,
               a.name AS account, t.account_id, t.id, t.is_internal_transfer, t.transfer_pair_id,
               c.name AS category, t.category_source
        FROM transactions t JOIN accounts a ON a.id = t.account_id LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.date BETWEEN ? AND ?
    """
    params: list[Any] = [start.isoformat(), end.isoformat()]
    if account_ids:
        sql += f" AND t.account_id IN ({','.join('?' * len(account_ids))})"
        params += account_ids
    if search:
        sql += " AND (t.description LIKE ? OR t.payee LIKE ? OR t.memo LIKE ? OR t.payee_normalized LIKE ?)"
        params += [f"%{search}%"] * 4
    if not include_pending:
        sql += " AND t.pending = 0"
    if exclude_internal:
        sql += " AND t.is_internal_transfer = 0"
    if category_id is not None:
        sql += " AND t.category_id = ?"
        params.append(category_id)
    if uncategorized:
        sql += " AND t.category_id IS NULL"
    sql += " ORDER BY t.date DESC LIMIT ?"
    params.append(limit)
    return [
        dict(r)
        | {
            "amount": round(r["amount"], 2),
            "pending": bool(r["pending"]),
            "is_internal_transfer": bool(r["is_internal_transfer"]),
        }
        for r in conn.execute(sql, params)
    ]
