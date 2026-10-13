import pytest

from personal_financial_mcp.categories import same_merchant
from personal_financial_mcp.normalize import (
    SEED_ALIASES, SEED_DESCRIPTION_ALIASES, clean_description, compile_aliases, normalize_payee,
)

ALIASES = compile_aliases(
    [(p, n, "description") for p, n in SEED_DESCRIPTION_ALIASES] + [(p, n, "payee") for p, n in SEED_ALIASES]
)

# (payee from SimpleFIN or None, raw bank description, expected payee_normalized). Anonymized, made-up refs.
CASES = [
    ("Apple", "PURCHASE AUTHORIZED ON 03/02 APPLE.COM/BILL 866-555-0100 CA S100000000000001 CARD 1111", "Apple"),
    (None, "PURCHASE AUTHORIZED ON 03/05 UBR* PENDING.UBER.COM CA S100000000000002 CARD 1111", "Uber"),
    ("Uber Pending Transaction", "PURCHASE AUTHORIZED ON 03/05 UBR* PENDING.UBER.AMSTERDAM NLD S100000000000003 CARD 1111", "Uber"),
    ("Money Transfer From Uber Ca", "MONEY TRANSFER AUTHORIZED ON 03/05 FROM Uber Uber CA P000000100000004 CARD 1111", "Uber Payout"),
    ("Costco Gas", "PURCHASE AUTHORIZED ON 03/06 COSTCO GAS #0001 SAN DIEGO CA P000000100000005 CARD 2222", "Costco Gas"),
    ("Costco", "PURCHASE INTL AUTHORIZED ON 03/07 COSTCO TIJUANA TIJUANA BCN MEX S100000000000006 CARD 2222", "Costco"),
    ("Capital One", "CAPITAL ONE MOBILE PMT CA0ABC123DEF456 JANE DOE", "Capital One Credit Card"),
    ("Capital One Auto Loan", "CAPITAL ONE AUTO DIRECTPAY DP0ABC123DEF JANE DOE", "Capital One Auto Loan"),
    ("Intl Tacos Lupita Tijuana Mex", "PURCHASE INTL AUTHORIZED ON 03/08 TACOS LUPITA TIJUANA BCN MEX S100000000000007 CARD 1111", "Tacos Lupita"),
    ("Recurring Payment Ca Luv Car Wash", "RECURRING PAYMENT AUTHORIZED ON 03/13 CA0001 LUV CAR WAS 800-555-0101 CA S100000000000008 CARD 1111", "Luv Car Wash"),
    ("Carnival Supermarket National City Ca", "PURCHASE AUTHORIZED ON 03/09 CARNIVAL SUPERMARK NATIONAL CITY CA S100000000000009 CARD 1111", "Carnival Supermarket"),
    ("Blue Owl Cafe San Diego Ca", "PURCHASE AUTHORIZED ON 03/10 TST*BLUE OWL CAFE San Diego CA S100000000000010 CARD 1111", "Blue Owl Cafe"),
    (None, "PURCHASE AUTHORIZED ON 03/01 BLUE OWL CAFE SAN DIEGO CA S100000000000011 CARD 1111", "Blue Owl Cafe"),
    ("Aldi", "PURCHASE AUTHORIZED ON 03/04 ALDI INC CHULA VISTA CA S100000000000012 CARD 1111", "Aldi"),
    # Pending charges: the merchant name is cut to ~12 characters.
    ("Carnival Sup Chula", "PURCHASE CARNIVAL SUP Chula Vista   CA CARD1111", "Carnival Supermarket"),
    ("Microsoft Re", "PURCHASE MICROSOFT*RE +18005550102  WA CARD1111", "Microsoft"),
    ("Transfer Uber Money", "TRANSFER Uber         Money Transf  CA CARD1111", "Uber Payout"),
    # Zelle: SimpleFIN names the payee after a brand in the sender's note.
    ("Minecraft", "ZELLE TO  DOE J ON 03/01 REF # WFCT00000001 MINECRAFT REALM", "Zelle Transfer"),
    ("Transfer to Apple Pay", "ZELLE TO  DOE J ON 03/01 REF # WFCT00000002 APPLE CASH", "Zelle Transfer"),
]


@pytest.mark.parametrize("payee,description,expected", CASES)
def test_normalize_payee(payee, description, expected):
    assert normalize_payee(payee, description, ALIASES) == expected


def test_clean_description_strips_prefix_refs_and_spaces():
    raw = "PURCHASE   AUTHORIZED ON   03/02 SOME SHOP 619-555-0123 CA S100000000000013 CARD 1111"
    assert clean_description(raw) == "Some Shop Ca"


def test_mobile_payment_is_not_a_gas_station():
    # "MOBILE PMT" contains "Mobil": a loose substring rule would have called this gasoline.
    assert normalize_payee(None, "CAPITAL ONE MOBILE PMT CA0ABC JANE DOE", ALIASES) == "Capital One Credit Card"


def test_description_quoting_another_charge_does_not_hijack_the_payee():
    # Regression: an overdraft fee's description names the charge that caused it.
    desc = "OVERDRAFT FEE FOR A TRANSACTION POSTED ON 03/31 $12.99 RECURRING PAYMENT AUTHORIZED ON 03/30 LUV CAR WAS"
    assert normalize_payee("Overdraft Fee", desc, ALIASES) == "Overdraft Fee"


def test_description_field_alias_matches_generic_payee():
    aliases = compile_aliases([(r"ONLINE TRANSFER (TO|FROM) DOE J\b", "Transfer Doe J", "description")]) + ALIASES
    desc = "ONLINE TRANSFER TO DOE J EVERYDAY CHECKING XXXXXX1234 REF #IB0ABC ON 03/01/26"
    assert normalize_payee("Online Transfer", desc, aliases) == "Transfer Doe J"
    assert normalize_payee("Online Transfer", "ONLINE TRANSFER TO ROE K ...", aliases) == "Online Transfer"


def test_empty_inputs():
    assert normalize_payee(None, None, ALIASES) == ""


@pytest.mark.parametrize("a,b,same", [
    ("sunrise mark", "sunrise market", True),  # pending cut mid-word
    ("carnival sup chula", "carnival supermarket", True),  # same first 12 characters
    ("microsoft re", "microsoft", True),  # whole word prefix
    ("zqx", "zqx shop", False),  # too short to trust
    ("costco", "costco gas", True),
    ("sunrise brewi", "sunrise market", False),
])
def test_same_merchant(a, b, same):
    assert same_merchant(a, b) is same
