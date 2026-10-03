import json
from datetime import date, timedelta

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from personal_financial_mcp import server

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
