import json
import sqlite3

from personal_financial_mcp import db

LEGACY_SCHEMA = """
CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, institution TEXT, currency TEXT, balance REAL,
                       available_balance REAL, balance_date TEXT, synced_at TEXT);
CREATE TABLE transactions (account_id TEXT NOT NULL REFERENCES accounts(id), id TEXT NOT NULL, date TEXT NOT NULL,
                           posted TEXT, amount REAL NOT NULL, description TEXT, payee TEXT, memo TEXT,
                           pending INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (account_id, id));
INSERT INTO accounts (id, name) VALUES ('ACC-A', 'Checking A');
INSERT INTO transactions (account_id, id, date, amount, description, payee)
VALUES ('ACC-A', 'T1', '2026-03-01', -12.5, 'PURCHASE AUTHORIZED ON 03/01 UBR* PENDING.UBER.COM CA', 'Uber Pending Transaction'),
       ('ACC-A', 'T2', '2026-03-02', -8.0, 'PURCHASE AUTHORIZED ON 03/02 SHOP', 'Corner Shop San Diego Ca');
"""


def test_legacy_db_is_backed_up_migrated_and_backfilled(tmp_db):
    legacy = sqlite3.connect(db.DB_PATH)
    legacy.executescript(LEGACY_SCHEMA)
    legacy.close()

    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
        rows = dict(conn.execute("SELECT id, payee_normalized FROM transactions").fetchall())
    assert rows == {"T1": "Uber", "T2": "Corner Shop"}

    backups = list((tmp_db / "backups").glob("finance-*-v0.db"))
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])
    assert old.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    old.close()


def test_migrations_are_idempotent_and_fresh_db_has_no_backup(tmp_db):
    with db.connect():
        pass
    db._initialized.clear()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert not (tmp_db / "backups").exists()


def test_local_aliases_override_seeds_and_renormalize(tmp_db):
    legacy = sqlite3.connect(db.DB_PATH)
    legacy.executescript(LEGACY_SCHEMA)
    legacy.close()
    with db.connect():
        pass

    db.LOCAL_RULES_FILE.write_text(json.dumps({"payee_aliases": [{"pattern": "^Corner Shop", "normalized": "Corner Shop Co"}]}))
    db._initialized.clear()
    with db.connect() as conn:
        rows = dict(conn.execute("SELECT id, payee_normalized FROM transactions").fetchall())
        sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM payee_aliases")}
    assert rows["T2"] == "Corner Shop Co"
    assert sources == {"seed", "local"}


def test_connections_are_closed(tmp_db):
    with db.connect() as conn:
        pass
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return
    raise AssertionError("connection should be closed after the with block")
