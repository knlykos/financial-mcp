import pytest

from personal_financial_mcp import db, server
from personal_financial_mcp.transfers import match_internal_transfers

from .conftest import make_account, make_tx

TO_B = "ONLINE TRANSFER TO DOE J EVERYDAY CHECKING XXXXXX2222 REF #IB0AAA ON 03/01/26"
FROM_A = "ONLINE TRANSFER FROM DOE J EVERYDAY CHECKING XXXXXX1111 REF #IB0BBB ON 03/01/26"
TO_STRANGER = "ONLINE TRANSFER TO ROE K EVERYDAY CHECKING XXXXXX9999 REF #IB0CCC ON 03/01/26"
FROM_STRANGER = "ONLINE TRANSFER FROM ROE K EVERYDAY CHECKING XXXXXX9999 REF #IB0DDD ON 03/01/26"


def sync(fake, a_txs, b_txs):
    fake.accounts = [
        make_account("ACC-A", "EVERYDAY CHECKING ...1111 (1111)", "100.00", a_txs),
        make_account("ACC-B", "EVERYDAY CHECKING ...2222 (2222)", "100.00", b_txs),
    ]
    return server.sync_transactions()


def flags():
    with db.connect() as conn:
        return {
            r["id"]: (bool(r["is_internal_transfer"]), r["transfer_pair_id"])
            for r in conn.execute("SELECT id, is_internal_transfer, transfer_pair_id FROM transactions")
        }


@pytest.fixture
def setup(tmp_db, fake_simplefin):
    return fake_simplefin


def test_perfect_pair(setup):
    result = sync(setup, [make_tx("out", 5, "-100.00", TO_B, "Online Transfer")],
                  [make_tx("in", 5, "100.00", FROM_A, "Online Transfer")])
    f = flags()
    assert result["internal_transfer_pairs"] == 1
    assert f["out"][0] and f["in"][0] and f["out"][1] == f["in"][1] is not None


def test_one_day_apart_still_pairs(setup):
    sync(setup, [make_tx("out", 5, "-80.00", TO_B)], [make_tx("in", 4, "80.00", FROM_A)])
    assert flags()["out"][0]


def test_three_days_apart_does_not_pair(setup):
    sync(setup, [make_tx("out", 10, "-70.00", TO_B)], [make_tx("in", 7, "70.00", FROM_A)])
    assert flags() == {"out": (False, None), "in": (False, None)}


def test_transfer_to_third_party_is_not_internal(setup):
    # Same amount and day on both own accounts, but both name an account that isn't ours.
    sync(setup, [make_tx("out", 3, "-40.00", TO_STRANGER)], [make_tx("in", 3, "40.00", FROM_STRANGER)])
    assert flags() == {"out": (False, None), "in": (False, None)}


def test_tie_closest_date_wins(setup):
    sync(
        setup,
        [make_tx("out-far", 6, "-25.00", TO_B), make_tx("out-near", 4, "-25.00", TO_B)],
        [make_tx("in", 4, "25.00", FROM_A)],
    )
    f = flags()
    assert f["out-near"][0] and f["in"][0] and f["out-near"][1] == f["in"][1]
    assert f["out-far"] == (False, None)


def test_same_account_is_not_a_transfer_pair(setup):
    sync(setup, [make_tx("out", 2, "-15.00", TO_B), make_tx("back", 2, "15.00", FROM_A)], [])
    assert not any(internal for internal, _ in flags().values())


def test_purchase_with_same_amount_is_not_paired(setup):
    sync(
        setup,
        [make_tx("buy", 2, "-50.00", "PURCHASE AUTHORIZED ON 03/01 COSTCO SAN DIEGO CA", "Costco")],
        [make_tx("in", 2, "50.00", FROM_A)],
    )
    assert not any(internal for internal, _ in flags().values())


def test_exclude_internal_and_rematch_is_idempotent(setup):
    sync(
        setup,
        [make_tx("out", 5, "-100.00", TO_B), make_tx("coffee", 1, "-4.50", "PURCHASE COFFEE", "Coffee")],
        [make_tx("in", 5, "100.00", FROM_A)],
    )
    everything = server.get_transactions()
    real = server.get_transactions(exclude_internal=True)
    assert everything["count"] == 3
    assert [t["id"] for t in real["transactions"]] == ["coffee"]

    before = flags()
    with db.connect() as conn:
        assert match_internal_transfers(conn) == 1
    assert flags() == before


# --- Credit card payments ---------------------------------------------------------------------------

CARD_PMT_OUT = "CAPITAL ONE      MOBILE PMT        CA0AAA JANE DOE"
CARD_PMT_IN = "CAPITAL ONE MOBILE PYMT"


def sync_with_card(fake, checking_txs, card_txs, card_name="Quicksilver (4645)", card_balance="-500.00"):
    fake.accounts = [
        make_account("ACC-A", "EVERYDAY CHECKING ...1111 (1111)", "100.00", checking_txs),
        make_account("CARD", card_name, card_balance, card_txs),
    ]
    return server.sync_transactions()


def test_account_kind_is_inferred(setup):
    sync_with_card(setup, [], [])
    with db.connect() as conn:
        kinds = {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM accounts")}
    assert kinds == {"ACC-A": "depository", "CARD": "credit"}
    assert db.infer_account_kind("Nefi", "Apple Card (Updated Monthly)", 0) == "credit"
    assert db.infer_account_kind("EVERYDAY CHECKING ...1111 (1111)", "Wells Fargo", -20) == "depository"
    assert db.infer_account_kind("Mystery", "Some Bank", -20) == "credit"


def test_card_payment_pairs_without_transfer_word(setup):
    result = sync_with_card(
        setup,
        [make_tx("pay", 3, "-25.00", "CAPITAL ONE      CRCARDPMT         CA0BBB JANE DOE", "Capital One Credit Card Payment")],
        [make_tx("got", 3, "25.00", "CAPITAL ONE AUTOPAY PYMT", "Capital One Credit Card")],
    )
    f = flags()
    assert result["internal_transfer_pairs"] == 1
    assert f["pay"][0] and f["got"][0] and f["pay"][1] == f["got"][1]


def test_card_payment_pairs_up_to_four_days_apart(setup):
    # Apple Card posts the payment before Wells Fargo does.
    sync_with_card(
        setup,
        [make_tx("pay", 7, "-83.29", "APPLECARD GSBANK PAYMENT    073126 00000002        JANE DOE", "Apple Credit Card")],
        [make_tx("got", 10, "83.29", "ACH Deposit Internet transfer from account ending in 1111", "Internet Transfer")],
        card_name="Jane", card_balance="-900.00",
    )
    assert flags()["pay"][0]


def test_card_payment_five_days_apart_does_not_pair(setup):
    sync_with_card(setup, [make_tx("pay", 10, "-40.00", CARD_PMT_OUT)], [make_tx("got", 5, "40.00", CARD_PMT_IN)])
    assert flags() == {"pay": (False, None), "got": (False, None)}


def test_payment_words_between_checking_accounts_do_not_pair(setup):
    # Only payments landing on an own credit card skip the TRANSFER requirement.
    sync(setup, [make_tx("out", 2, "-60.00", "OPORTUN PAYMENT 123")], [make_tx("in", 2, "60.00", "SOME PAYMENT REFUND")])
    assert flags() == {"out": (False, None), "in": (False, None)}


def test_debit_purchase_is_not_a_card_payment(setup):
    # "RECURRING PAYMENT AUTHORIZED ON ..." is a debit card purchase; a same-amount card refund must not pair with it.
    sync_with_card(
        setup,
        [make_tx("buy", 2, "-13.99", "RECURRING PAYMENT AUTHORIZED ON 07/05 APPLE.COM/BILL 866-555-0100 CA", "Apple")],
        [make_tx("refund", 2, "13.99", "APPLE.COM/BILL PAYMENT REVERSAL")],
    )
    assert flags() == {"buy": (False, None), "refund": (False, None)}


def test_paired_card_payment_is_not_counted_as_debt_twice(setup):
    sync_with_card(
        setup,
        [
            make_tx("pay", 3, "-100.00", CARD_PMT_OUT, "Capital One"),
            make_tx("other-card", 4, "-50.00", "CAPITAL ONE      ONLINE PMT        CA0CCC JANE DOE", "Capital One"),
        ],
        [make_tx("got", 3, "100.00", CARD_PMT_IN, "Capital One"), make_tx("old", 20, "25.00", CARD_PMT_IN, "Capital One")],
    )
    rows = {t["id"]: t["category"] for t in server.get_transactions(fields=["id", "category"], limit=100)["transactions"]}
    assert rows["pay"] == rows["got"] == "Transferencia interna"
    assert rows["other-card"] == "Tarjetas de crédito"  # a card that isn't connected
    assert rows["old"] is None  # card side whose checking side isn't in the data: not debt, not income
