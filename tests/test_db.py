import json

import pytest

from personal_financial_mcp import db

from .conftest import make_account, make_tx


def store(accounts):
    with db.connect() as conn:
        db.upsert_accounts(conn, accounts)
        db.upsert_transactions(conn, accounts)


def test_migrations_are_idempotent(tmp_db):
    with db.connect() as conn:
        assert db.schema_version(conn) == len(db.MIGRATIONS)
    db._initialized.clear()
    with db.connect() as conn:
        assert db.schema_version(conn) == len(db.MIGRATIONS)
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


def test_failed_migration_rolls_back_everything(tmp_db, monkeypatch):
    def broken(conn):
        conn.execute("CREATE TABLE half_done (id int)")
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, broken])
    with pytest.raises(RuntimeError, match="boom"):
        with db.connect():
            pass

    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:-1])
    with db.connect() as conn:
        assert db.schema_version(conn) == len(db.MIGRATIONS)
        assert conn.execute("SELECT to_regclass('half_done')").fetchone()[0] is None


def test_rows_read_by_name_position_and_as_dict(tmp_db):
    with db.connect() as conn:
        row = conn.execute("SELECT 1 AS one, 'x' AS two").fetchone()
    assert (row["one"], row[1], dict(row), tuple(row)) == (1, "x", {"one": 1, "two": "x"}, (1, "x"))


def test_dates_come_back_as_iso_text(tmp_db):
    store([make_account("ACC-A", "Checking A", "10.00", [make_tx("T1", 3, "-5.00", "SHOP", "Shop")])])
    with db.connect() as conn:
        row = conn.execute("SELECT t.date, a.balance_date, a.synced_at FROM transactions t, accounts a").fetchone()
    assert all(isinstance(v, str) for v in row)
    assert len(row["date"]) == 10


def test_local_aliases_override_seeds_and_renormalize(tmp_db):
    store([
        make_account("ACC-A", "Checking A", "10.00", [
            make_tx("T1", 1, "-12.50", "PURCHASE AUTHORIZED ON 03/01 UBR* PENDING.UBER.COM CA", "Uber Pending Transaction"),
            make_tx("T2", 2, "-8.00", "PURCHASE AUTHORIZED ON 03/02 SHOP", "Corner Shop San Diego Ca"),
        ]),
    ])
    with db.connect() as conn:
        rows = dict(conn.execute("SELECT id, payee_normalized FROM transactions").fetchall())
    assert rows == {"T1": "Uber", "T2": "Corner Shop"}

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
    assert conn.closed


def test_missing_database_url_is_a_clear_error(tmp_db, monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setattr(db, "DATABASE_URL_FILE", tmp_path / "missing")
    with pytest.raises(Exception, match="No database configured"):
        with db.connect():
            pass
