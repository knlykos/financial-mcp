import json
from datetime import date, timedelta

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from personal_financial_mcp import server

from .conftest import make_account, make_tx

ORIGINAL_FIELDS = {"date", "amount", "description", "payee", "memo", "pending", "account", "account_id", "id"}


def test_sync_stores_payee_normalized(tmp_db, fake_simplefin, sample_accounts):
    fake_simplefin.accounts = sample_accounts
    result = server.sync_transactions(days=30)
    assert result["total_stored"] == 4
    by_id = {t["id"]: t for t in server.get_transactions()["transactions"]}
    assert by_id["T1"]["payee_normalized"] == "Apple"
    assert by_id["T3"]["payee_normalized"] == "Blue Owl Cafe"  # no payee: cleaned from the description
    assert by_id["T4"]["payee_normalized"] == "Costco Gas"


def test_get_transactions_defaults_keep_original_fields(tmp_db, fake_simplefin, sample_accounts):
    fake_simplefin.accounts = sample_accounts
    server.sync_transactions()
    result = server.get_transactions()
    assert result["count"] == 4
    assert ORIGINAL_FIELDS <= set(result["transactions"][0])
    assert [t["date"] for t in result["transactions"]] == sorted((t["date"] for t in result["transactions"]), reverse=True)


def test_fields_and_compact(tmp_db, fake_simplefin, sample_accounts):
    fake_simplefin.accounts = sample_accounts
    server.sync_transactions()

    only = server.get_transactions(fields=["date", "amount", "payee_normalized"])["transactions"]
    assert all(set(t) == {"date", "amount", "payee_normalized"} for t in only)

    compact = server.get_transactions(compact=True)["transactions"]
    assert all("description" not in t and "memo" not in t and "payee_normalized" in t for t in compact)

    full = server.get_transactions()
    small = server.get_transactions(fields=["date", "amount", "payee_normalized"])
    assert len(json.dumps(small)) < len(json.dumps(full)) / 2


def test_unknown_field_is_rejected(tmp_db):
    with pytest.raises(ToolError, match="Unknown fields"):
        server.get_transactions(fields=["date", "secret"])


def test_search_matches_normalized_payee(tmp_db, fake_simplefin, sample_accounts):
    fake_simplefin.accounts = sample_accounts
    server.sync_transactions()
    hits = server.get_transactions(search="blue owl", start_date=date.today() - timedelta(days=60))
    assert [t["id"] for t in hits["transactions"]] == ["T3"]


def test_reads_do_not_call_simplefin(tmp_db, fake_simplefin, sample_accounts):
    fake_simplefin.accounts = sample_accounts
    server.sync_transactions()
    server.list_accounts()
    calls_before = len(fake_simplefin.calls)
    server.get_transactions(compact=True)
    server.list_accounts()
    assert len(fake_simplefin.calls) == calls_before


def checking(balance, available, pending_amounts):
    acc = make_account("ACC-W", "EVERYDAY CHECKING ...0001 (0001)", balance, [])
    acc["available-balance"] = available
    acc["transactions"] = [
        make_tx(f"P{i}", 0, amount, "PURCHASE AUTHORIZED ON 10/06 SOME STORE", pending=True)
        for i, amount in enumerate(pending_amounts)
    ]
    return acc


def balances():
    (acc,) = server.list_accounts()
    return acc["balance"], acc["available_balance"]


@pytest.mark.parametrize(
    "reported, pending, expected",
    [
        (("662.25", "19.72"), ["-500.00", "-181.97"], (662.25, -19.72)),  # available dropped its minus
        (("64.32", "1911.95"), ["1976.27"], (-64.32, 1911.95)),  # overdrawn, payroll pending
        (("64.32", "64.32"), [], (64.32, 64.32)),  # no pending: nothing to tell signs apart
        (("100.00", "40.00"), ["-60.00"], (100.0, 40.0)),  # already consistent
        (("100.00", "35.00"), ["-60.00"], (100.0, 35.0)),  # unexplained hold: leave as reported
    ],
)
def test_sync_restores_dropped_minus_on_checking(tmp_db, fake_simplefin, reported, pending, expected):
    fake_simplefin.accounts = [checking(*reported, pending)]
    server.sync_transactions()
    assert balances() == expected


def test_credit_card_balance_kept_and_zero_available_is_unknown(tmp_db, fake_simplefin):
    card = make_account("ACC-C", "Quicksilver (0002)", "-500.00", [])
    card["available-balance"] = "0.00"  # Capital One sends 0 when it doesn't share available credit
    fake_simplefin.accounts = [card]
    server.sync_transactions()
    assert balances() == (-500.0, None)


def test_credit_card_real_available_credit_is_kept(tmp_db, fake_simplefin):
    card = make_account("ACC-C", "Quicksilver (0002)", "-500.00", [])
    card["available-balance"] = "1500.00"
    fake_simplefin.accounts = [card]
    server.sync_transactions()
    assert balances() == (-500.0, 1500.0)
