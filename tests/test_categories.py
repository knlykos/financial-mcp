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


def test_rules_count_only_what_they_matched(synced):
    rules = server.manage_rules(action="list")
    costco = next(r for r in rules if r["pattern"] == "Costco" and r["source"] == "seed")
    grocery = next(r for r in rules if r["pattern"] == "Grocery")  # same category, matched nothing
    assert costco["transactions"] == 1
    assert grocery["transactions"] == 0

    server.set_category(["costco"], "Compras")  # manual: no longer counted for the rule
    costco = next(r for r in server.manage_rules(action="list") if r["id"] == costco["id"])
    assert costco["transactions"] == 0


def test_zelle_note_and_unnamed_paypal_purchase(tmp_db, fake_simplefin):
    fake_simplefin.accounts = [make_account("ACC-A", "CHECKING ...1111 (1111)", "100.00", [
        make_tx("zelle-note", 1, "-18.00", "ZELLE TO  DOE J ON 03/01 REF # WFCT00000001 MINECRAFT REALM", "Minecraft"),
        make_tx("paypal", 2, "-10.59", "PAYPAL           PURCHASE   260301 SOMESHOPXYZ SOM JANE DOE", "PayPal"),
        make_tx("paypal-parts", 3, "-55.00", "PAYPAL           PURCHASE   260301 AUTOZONEINC JANE DOE", "Autozoneinc"),
        make_tx("paypal-card", 4, "-100.00", "PAYPAL           INST XFER  260301 PPCR CC REPAYME JANE DOE", "PayPal"),
    ])]
    server.sync_transactions()
    assert cat("zelle-note")[0] == "Envíos de dinero"
    assert cat("paypal")[0] == "Compras"
    assert cat("paypal-parts")[0] == "Mantenimiento auto"  # a known merchant behind PayPal keeps its category
    assert cat("paypal-card")[0] == "Tarjetas de crédito"


def test_list_categories_counts_uncategorized(synced):
    rows = {c["name"]: c for c in server.list_categories()}
    assert rows["(sin categoría)"]["transactions"] == 1
    assert rows["Transferencia interna"]["total"] == 0


def test_card_interest_cashback_and_dealer(tmp_db, fake_simplefin):
    fake_simplefin.accounts = [
        make_account("ACC-A", "CHECKING ...1111 (1111)", "100.00", [
            make_tx("loan", 1, "-436.38", "NISSAN RET       Auto Loan  260908 0000000001      DOE JANE", "Nissan"),
        ]),
        make_account("CARD", "Platinum (0003)", "-300.00", [
            make_tx("interest", 1, "-7.54", "INTEREST CHARGE:PURCHASES", "Interest Charge"),
            make_tx("cashback", 2, "24.72", "CREDIT-CASH BACK REWARD", "Cashback"),
            make_tx("dealer", 3, "-27.64", "MOSSY NISSAN CHULA VIS", "Nissan"),
            make_tx("parts", 4, "-55.00", "PAYPAL *AUTOZONEINC", "Autozoneinc"),
            make_tx("mcd", 5, "-8.19", "MC DONALD S BELLAS A", "Donald S Bellas"),
            make_tx("motel", 6, "-43.06", "MOTEL LAS PALMAS TIJUANA", "Motel Las Palmas"),
        ]),
    ]
    server.sync_transactions()
    assert cat("loan")[0] == "Préstamo auto"
    assert cat("interest")[0] == "Intereses"
    assert cat("cashback")[0] == "Otros ingresos"
    assert cat("dealer") == (None, "none")  # a bare "Nissan" purchase is not a loan payment
    assert cat("parts")[0] == "Mantenimiento auto"
    assert cat("mcd")[0] == "Comida fuera"
    assert cat("motel")[0] == "Hospedaje"


def sync_more(fake, *txs):
    fake.accounts[0]["transactions"] += list(txs)
    server.sync_transactions()


def test_history_names_a_cut_pending_charge(synced):
    sync_more(
        synced,
        make_tx("market", 9, "-20.00", "PURCHASE AUTHORIZED ON 03/01 ZQX CORNER MARKET", "Zqx Corner Market"),
        make_tx("pending", 0, "-5.00", "PURCHASE ZQX CORNER M CHULA VISTA CA CARD1111", "Zqx Corner M", pending=True),
    )
    assert cat("market") == ("Súper", "rule")
    assert cat("pending") == ("Súper", "history")


def test_manual_choice_teaches_history(synced):
    server.set_category(["mystery"], "Compras")
    sync_more(synced, make_tx("mystery-2", 0, "-9.00", "PURCHASE AUTHORIZED ON 03/09 ZQX SHOP", "Zqx Shop"))
    assert cat("mystery-2") == ("Compras", "history")


def test_history_skips_mixed_merchants_and_description_rules(synced):
    server.set_category(["mystery"], "Compras")
    sync_more(
        synced,
        make_tx("mystery-2", 1, "-9.00", "ZQX SHOP", "Zqx Shop"),
        make_tx("mystery-3", 0, "-7.00", "ZQX SHOP", "Zqx Shop"),
        # Matched by a description rule: says nothing about other "Nissan" charges.
        make_tx("loan", 2, "-400.00", "NISSAN RET       Auto Loan  260908 0000000001      DOE JANE", "Nissan"),
        make_tx("dealer", 0, "-27.64", "MOSSY NISSAN CHULA VIS", "Nissan"),
    )
    server.set_category(["mystery-2"], "Salud")  # now "Zqx Shop" has two categories
    server.recategorize()
    assert cat("mystery-3") == (None, "none")
    assert cat("loan")[0] == "Préstamo auto"
    assert cat("dealer") == (None, "none")


def test_remember_creates_a_rule_for_the_merchant(synced):
    result = server.set_category(["mystery"], "Compras", remember=True)
    assert result["rules_created"] == [{"merchant": "Zqx Shop", "direction": "out"}]
    assert result["set_manually"] == 0
    assert cat("mystery") == ("Compras", "rule")

    # A later pending charge from the same merchant is categorized by the rule, and the rule is replaced, not
    # duplicated, when the merchant is remembered again.
    sync_more(synced, make_tx("pending", 0, "-3.00", "ZQX SHOP", "Zqx Shop", pending=True))
    assert cat("pending") == ("Compras", "rule")
    server.set_category(["pending"], "Salud", remember=True)
    assert cat("mystery") == ("Salud", "rule")
    user_rules = [r for r in server.manage_rules(action="list") if r["source"] == "user"]
    assert [(r["pattern"], r["category"], r["transactions"]) for r in user_rules] == [("Zqx Shop", "Salud", 2)]


def test_review_uncategorized_groups_by_merchant(synced):
    sync_more(synced, make_tx("mystery-in", 0, "12.00", "ZQX SHOP REFUND", "Zqx Shop"))
    review = server.review_uncategorized()
    groups = {(g["merchant"], g["direction"]): g for g in review["groups"]}
    assert set(groups) == {("Zqx Shop", "out"), ("Zqx Shop", "in")}
    out = groups[("Zqx Shop", "out")]
    assert out["transaction_ids"] == ["mystery"] and out["total"] == -12.0
    assert out["examples"][0]["description"].endswith("ZQX SHOP")
    assert "Súper" in review["categories"]["expense"]


def test_review_can_include_history_guesses(synced):
    server.set_category(["mystery"], "Compras")
    sync_more(synced, make_tx("mystery-2", 0, "-9.00", "ZQX SHOP", "Zqx Shop"))
    assert server.review_uncategorized()["groups"] == []
    (group,) = server.review_uncategorized(include_guessed=True)["groups"]
    assert group["transaction_ids"] == ["mystery-2"] and group["guessed_category"] == "Compras"


def test_sync_points_to_review_when_something_is_left(synced):
    result = server.sync_transactions()
    assert result["uncategorized"] == 1 and "review_uncategorized" in result["next_step"]
