"""Shared fixtures. All data here is synthetic: no real bank data in tests.

Tests need a throwaway Postgres (e.g. a Neon "test" branch): TEST_DATABASE_URL, else .test-database-url.
Each test gets its own schema, dropped afterwards.
"""

import os
import time
import uuid
from typing import Any

import psycopg
import pytest

from personal_financial_mcp import db, server
from personal_financial_mcp.simplefin import PROJECT_ROOT

TEST_DATABASE_URL_FILE = PROJECT_ROOT / ".test-database-url"

NOW = int(time.time())
DAY = 86_400


def make_tx(tx_id: str, days_ago: int, amount: str, description: str, payee: str | None = None, **extra: Any):
    tx = {"id": tx_id, "posted": NOW - days_ago * DAY, "amount": amount, "description": description, **extra}
    if payee is not None:
        tx["payee"] = payee
    return tx


def make_account(acc_id: str, name: str, balance: str, transactions: list[dict[str, Any]]):
    return {
        "org": {"name": "Test Bank", "sfin-url": "https://example.invalid"},
        "id": acc_id,
        "name": name,
        "currency": "USD",
        "balance": balance,
        "available-balance": balance,
        "balance-date": NOW,
        "transactions": transactions,
    }


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url and TEST_DATABASE_URL_FILE.exists():
        url = TEST_DATABASE_URL_FILE.read_text().strip()
    if not url:
        pytest.fail(f"Set TEST_DATABASE_URL or put a throwaway Postgres URL in {TEST_DATABASE_URL_FILE}")
    return url


@pytest.fixture
def tmp_db(tmp_path, monkeypatch, test_database_url):
    """Point the app at an empty schema of the test database and no local rules file."""
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    sep = "&" if "?" in test_database_url else "?"
    monkeypatch.setenv("DATABASE_URL", f"{test_database_url}{sep}options=-csearch_path%3D{schema}")
    monkeypatch.setattr(db, "LOCAL_RULES_FILE", tmp_path / "local_rules.json")
    monkeypatch.setattr(db, "_initialized", set())
    yield tmp_path
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def fake_simplefin(monkeypatch):
    """Replace the SimpleFIN client; tests set `.accounts` and inspect `.calls`."""

    class Fake:
        calls: list[dict[str, Any]] = []
        accounts: list[dict[str, Any]] = []

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            return {"errors": [], "accounts": self.accounts}

    fake = Fake()
    fake.calls = []
    monkeypatch.setattr(server, "fetch_accounts", fake)
    return fake


@pytest.fixture
def sample_accounts():
    return [
        make_account(
            "ACC-A",
            "Checking A",
            "500.00",
            [
                make_tx("T1", 2, "-45.10", "PURCHASE AUTHORIZED ON 03/02 APPLE.COM/BILL 866-555-0100 CA S100000000000001 CARD 1111", "Apple"),
                make_tx("T2", 5, "1500.00", "ACME CORP PAYROLL 260301 999 DOE, JANE", "Acme Corp Payroll Doe Jane"),
                make_tx("T3", 8, "-30.00", "PURCHASE AUTHORIZED ON 03/01 BLUE OWL CAFE SAN DIEGO CA S100000000000002 CARD 1111"),
            ],
        ),
        make_account(
            "ACC-B",
            "Checking B",
            "20.00",
            [make_tx("T4", 3, "-60.25", "PURCHASE AUTHORIZED ON 03/03 COSTCO GAS #0001 SAN DIEGO CA P000000100000003 CARD 2222", "Costco Gas")],
        ),
    ]
