"""One-off: copy the old SQLite store (data/finance.db) into the Postgres database from .database-url.

Keeps ids, so manual categories and user rules point at the same categories. Refuses to run if the
target already has transactions. Usage: uv run python scripts/import_sqlite.py [path/to/finance.db]
"""

import sqlite3
import sys
from pathlib import Path

from personal_financial_mcp import db, pg as pglib
from personal_financial_mcp.pg import executemany
from personal_financial_mcp.simplefin import PROJECT_ROOT

# (table, columns, columns stored as booleans in Postgres). Order respects foreign keys.
TABLES = [
    ("categories", ["id", "name", "parent", "kind", "source"], []),
    ("payee_aliases", ["id", "pattern", "normalized", "field", "priority", "source"], []),
    ("category_rules", ["id", "match_field", "match_type", "pattern", "category_id", "direction", "priority",
                        "source", "created_at"], []),
    ("accounts", ["id", "name", "institution", "currency", "balance", "available_balance", "balance_date",
                  "synced_at", "kind"], []),
    ("transactions", ["account_id", "id", "date", "posted", "amount", "description", "payee", "memo", "pending",
                      "payee_normalized", "is_internal_transfer", "transfer_pair_id", "category_id",
                      "category_source"], ["pending", "is_internal_transfer"]),
]
IDENTITY_TABLES = ["categories", "payee_aliases", "category_rules"]

SUMMARY = """
    SELECT COUNT(*), ROUND(SUM(amount)::numeric, 2)::float8, COUNT(*) FILTER (WHERE category_source = 'manual'),
           COUNT(*) FILTER (WHERE is_internal_transfer), COUNT(*) FILTER (WHERE category_id IS NULL)
    FROM transactions
"""
SQLITE_SUMMARY = """
    SELECT COUNT(*), ROUND(SUM(amount), 2), SUM(category_source = 'manual'), SUM(is_internal_transfer),
           SUM(category_id IS NULL)
    FROM transactions
"""


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "finance.db"
    lite = sqlite3.connect(source)
    lite.row_factory = sqlite3.Row
    if lite.execute("PRAGMA user_version").fetchone()[0] != 5:
        sys.exit("Open the SQLite DB with the last SQLite version of the server first (schema v5 expected).")

    with pglib.connect(db.database_url()) as pg:
        db._migrate(pg)
        if pg.execute("SELECT 1 FROM transactions LIMIT 1").fetchone():
            sys.exit("The target database already has transactions; not importing over them.")
        pg.execute("TRUNCATE category_rules, payee_aliases, categories, accounts CASCADE")

        for table, cols, bools in TABLES:
            rows = [
                tuple(bool(r[c]) if c in bools else r[c] for c in cols)
                for r in lite.execute(f"SELECT {', '.join(cols)} FROM {table}")
            ]
            placeholders = ", ".join(["%s"] * len(cols))
            executemany(pg, f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows)
            print(f"{table}: {len(rows)} rows")
        for table in IDENTITY_TABLES:
            pg.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT MAX(id) FROM {table}))")

        imported = tuple(pg.execute(SUMMARY).fetchone())
        db._refresh_derived(pg)  # same pass the server runs on start; shows what current rules change
        refreshed = tuple(pg.execute(SUMMARY).fetchone())

    print("               count, sum, manual, internal, uncategorized")
    print("sqlite:       ", tuple(lite.execute(SQLITE_SUMMARY).fetchone()))
    print("imported:     ", imported)
    print("after refresh:", refreshed)


if __name__ == "__main__":
    main()
