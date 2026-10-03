import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from personal_financial_mcp import db, server

from .conftest import make_account, make_tx

TO_B = "ONLINE TRANSFER TO DOE J EVERYDAY CHECKING XXXXXX2222 REF #IB0AAA ON 03/01/26"
FROM_A = "ONLINE TRANSFER FROM DOE J EVERYDAY CHECKING XXXXXX1111 REF #IB0BBB ON 03/01/26"


@pytest.fixture
def synced(tmp_db, fake_simplefin):
    fake_simplefin.accounts = [
        make_account("ACC-A", "CHECKING ...1111 (1111)", "100.00", [
            make_tx("mobile-pmt", 1, "-50.00", "CAPITAL ONE MOBILE PMT CA0ABC JANE DOE", "Capital One"),
            make_tx("gas", 2, "-30.00", "PURCHASE AUTHORIZED ON 03/02 MOBIL 123 SAN DIEGO CA", "Mobil"),
            make_tx("coffee", 3, "-6.00", "PURCHASE AUTHORIZED ON 03/03 FINJAN COFFEE", "Finjan Coffee San Diego Ca"),
            make_tx("od-fee", 3, "-35.00", "OVERDRAFT FEE FOR A TRANSACTION POSTED ON 03/02", "Overdraft Fee"),
            make_tx("payroll", 4, "2000.00", "ACME CORP PAYROLL 260301 DOE, JANE", "Acme Corp Payroll Doe Jane"),
            make_tx("zelle-in", 5, "40.00", "ZELLE FROM ROE K ON 03/01 REF # X1", "Zelle Transfer"),
            make_tx("zelle-out", 5, "-25.00", "ZELLE TO ROE K ON 03/01 REF # X2", "Zelle Transfer"),
            make_tx("mystery", 6, "-12.00", "PURCHASE AUTHORIZED ON 03/01 ZQX SHOP", "Zqx Shop"),
            make_tx("xfer-out", 7, "-100.00", TO_B, "Online Transfer"),
            make_tx("costco", 8, "-80.00", "PURCHASE AUTHORIZED ON 03/01 COSTCO WHSE", "Costco"),
        ]),
        make_account("ACC-B", "CHECKING ...2222 (2222)", "100.00", [
            make_tx("xfer-in", 7, "100.00", FROM_A, "Online Transfer"),
        ]),
    ]
    server.sync_transactions()
    return fake_simplefin


def cat(tx_id):
    rows = server.get_transactions(fields=["id", "category", "category_source"], limit=100)["transactions"]
    return next((r["category"], r["category_source"]) for r in rows if r["id"] == tx_id)


def test_mobile_payment_is_credit_card_not_gas(synced):
    assert cat("mobile-pmt") == ("Tarjetas de crédito", "rule")
    assert cat("gas")[0] == "Gasolina"


def test_contains_matches_whole_words_only(synced):
    assert cat("coffee")[0] == "Comida fuera"  # "Coffee" must not hit the bank-fee rule ("Fee")
    assert cat("od-fee")[0] == "Comisiones bancarias"


def test_direction_rules(synced):
    assert cat("payroll")[0] == "Nómina"
    assert cat("zelle-in")[0] == "Otros ingresos"
    assert cat("zelle-out")[0] == "Envíos de dinero"


def test_internal_transfer_category_beats_rules(synced):
    assert cat("xfer-out") == ("Transferencia interna", "rule")
    assert cat("xfer-in") == ("Transferencia interna", "rule")


def test_unmatched_is_uncategorized(synced):
    assert cat("mystery") == (None, "none")
    only = server.get_transactions(category="(sin categoría)", limit=100)["transactions"]
    assert [t["id"] for t in only] == ["mystery"]


def test_manual_wins_over_rules_and_survives_recategorize(synced):
    server.set_category(["costco"], "Compras")
    assert cat("costco") == ("Compras", "manual")
    server.recategorize()
    server.manage_rules(action="add", pattern="Costco", category="Gasolina", priority=1)
    assert cat("costco") == ("Compras", "manual")

    server.set_category(["costco"], None)  # clearing the manual choice re-applies rules
    assert cat("costco") == ("Gasolina", "rule")


def test_set_category_validates(synced):
    with pytest.raises(ToolError, match="Unknown category"):
        server.set_category(["costco"], "Nope")
    with pytest.raises(ToolError, match="Unknown transaction ids"):
        server.set_category(["does-not-exist"], "Compras")


def test_rule_preview_add_and_delete(synced):
    preview = server.manage_rules(action="test", pattern="Zqx Shop")
    assert preview["matches"] == 1 and preview["sample"][0]["id"] == "mystery"
    assert cat("mystery") == (None, "none")  # test never saves

    added = server.manage_rules(action="add", pattern="Zqx Shop", category="Compras")
    assert cat("mystery") == ("Compras", "rule")
    assert any(r["id"] == added["rule_id"] and r["source"] == "user" for r in server.manage_rules(action="list"))

    server.manage_rules(action="delete", rule_id=added["rule_id"])
    assert cat("mystery") == (None, "none")


def test_seed_rules_cannot_be_deleted(synced):
    seed = next(r for r in server.manage_rules(action="list") if r["source"] == "seed")
    with pytest.raises(ToolError, match="seed rule"):
        server.manage_rules(action="delete", rule_id=seed["id"])


def test_invalid_rule_inputs(synced):
    with pytest.raises(ToolError, match="Invalid regex"):
        server.manage_rules(action="test", pattern="(unclosed", match_type="regex")
    with pytest.raises(ToolError, match="category is required"):
        server.manage_rules(action="add", pattern="X")


def test_local_rules_file_adds_categories_and_rules(tmp_db, fake_simplefin):
    db.LOCAL_RULES_FILE.write_text(json.dumps({
        "categories": [{"name": "Pareja", "kind": "transfer"}],
        "category_rules": [{"pattern": "Zqx Shop", "category": "Pareja"}],
    }), encoding="utf-8")
    fake_simplefin.accounts = [make_account("ACC-A", "CHECKING ...1111", "1.00", [
        make_tx("mystery", 1, "-12.00", "ZQX SHOP", "Zqx Shop"),
    ])]
    server.sync_transactions()
    assert cat("mystery") == ("Pareja", "rule")
    kinds = {c["name"]: c["kind"] for c in server.list_categories()}
    assert kinds["Pareja"] == "transfer"


def test_list_categories_counts_uncategorized(synced):
    rows = {c["name"]: c for c in server.list_categories()}
    assert rows["(sin categoría)"]["transactions"] == 1
    assert rows["Transferencia interna"]["total"] == 0
