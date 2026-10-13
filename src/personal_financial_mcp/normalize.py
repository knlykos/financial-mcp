"""Merchant name normalization: payee_normalized = alias match, else a cleaned payee/description."""

import re
from dataclasses import dataclass

# Generic aliases shipped with the code. Personal ones (employer, own name) go in local_rules.json.
# Patterns are case-insensitive regexes; first match wins. They are tested against the payee when SimpleFIN
# provides one, and against the raw description only when it doesn't (or when an alias has field="description"):
# descriptions can quote other transactions, e.g. an overdraft fee naming the charge that caused it.
# Pending card charges arrive cut to ~12 characters ("PURCHASE CARNIVAL SUP Chula Vista CA"), so aliases also
# cover the cut name; otherwise a charge only gets its category once it posts.
SEED_ALIASES: list[tuple[str, str]] = [
    (r"^Uber Payout$|Money Transfer\s*(Authorized On \d\d/\d\d)?\s*From Uber|^Transfer Uber Money\b", "Uber Payout"),
    (r"^Uber( Amsterdam| Pending Transaction)?$|\bUBR\*|\bUBER\s*\*\s*(TRIP|EATS)", "Uber"),
    (r"^Apple$|APPLE\.COM/BILL", "Apple"),
    (r"^Costco Gas$|COSTCO GAS", "Costco Gas"),
    (r"^Costco$|\bCOSTCO\b", "Costco"),
    (r"^Capital One Auto Loan$|CAPITAL ONE AUTO", "Capital One Auto Loan"),
    (r"^Capital One( Credit Card( Payment)?)?$|CAPITAL ONE\s*(CRCARDPMT|MOBILE PMT|ONLINE PMT)", "Capital One Credit Card"),
    (r"^Apple Credit Card$|\bAPPLECARD\b", "Apple Credit Card"),
    (r"\bCARNIVAL SUP", "Carnival Supermarket"),
    (r"^Microsoft\b|\bMICROSOFT\s*\*", "Microsoft"),
    (r"\bALDI\b", "Aldi"),
    (r"OTAY FARMS", "Otay Farms Market"),
    (r"STARS GAS", "Stars Gas"),
    (r"LUV CAR WAS", "Luv Car Wash"),
    (r"\bCLOUDFLARE\b", "Cloudflare"),
    (r"\bANTHROPIC\b", "Anthropic"),
    (r"\bREMITLY\b|\bRMTLY\*", "Remitly"),
    (r"\bCALIMAX\b", "Calimax"),
    (r"\bAUTOZONE", "AutoZone"),  # "PAYPAL *AUTOZONEINC"
    (r"^San Diego Gas & Electric$|SD GAS & ELEC", "San Diego Gas & Electric"),
]

# Checked against the raw description, before SEED_ALIASES. For Zelle, SimpleFIN builds the payee from the
# sender's note, so "ZELLE TO ... MINECRAFT REALM" would otherwise become the merchant "Minecraft".
SEED_DESCRIPTION_ALIASES: list[tuple[str, str]] = [
    (r"^ZELLE (TO|FROM)\b", "Zelle Transfer"),
]

# Trailing "<city> Ca|Mex" that SimpleFIN appends to some payees. Public place names, not personal data.
CITIES = [
    "Chula Vista", "San Diego", "Imperial Beach", "Imperial Beac", "Beac", "National City", "Coronado",
    "San Ysidro", "Tijuana", "Hermosillo So", "Los Angeles",
]
_CITY_SUFFIX = re.compile(r"\s+(?:" + "|".join(re.escape(c) for c in CITIES) + r")\s+(?:Ca|Mex)$", re.I)
_PAYEE_PREFIX = re.compile(r"^(?:Intl|Recurring Payment(?: Ca)?)\s+", re.I)

_DESC_PREFIX = re.compile(
    r"^(?:PURCHASE(?: INTL| RETURN)?|RECURRING PAYMENT|MONEY TRANSFER|ATM WITHDRAWAL)\s*AUTHORIZED ON\s+\d\d/\d\d\s*",
    re.I,
)
_DESC_NOISE = [
    re.compile(r"[SP]\d{12,}"),  # authorization refs, often glued to the state: "CAS3562..."
    re.compile(r"REF\s*(?:NUMBER\s*:|#)\s*\S+", re.I),
    re.compile(r"\bCARD\s*\d{4}\b", re.I),
    re.compile(r"\bON\s+\d\d/\d\d(?:/\d\d)?\b", re.I),
    re.compile(r"\b\d{3}-\d{3}-\d{4}\b"),
    re.compile(r"\bX{4,}\d{4}\b", re.I),
    re.compile(r"\b\d{5,}\b"),
]


@dataclass(frozen=True)
class Alias:
    regex: re.Pattern[str]
    normalized: str
    field: str = "payee"  # payee | description


def compile_aliases(rows: list[tuple[str, str, str]]) -> list[Alias]:
    return [Alias(re.compile(pattern, re.I), normalized, field) for pattern, normalized, field in rows]


def clean_description(description: str) -> str:
    text = _DESC_PREFIX.sub("", description.strip())
    for rx in _DESC_NOISE:
        text = rx.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -*#:")
    return text.title()


def clean_payee(payee: str) -> str:
    text = re.sub(r"\s+", " ", payee).strip()
    text = _PAYEE_PREFIX.sub("", text)
    return _CITY_SUFFIX.sub("", text).strip()


def normalize_payee(payee: str | None, description: str | None, aliases: list[Alias]) -> str:
    payee = (payee or "").strip()
    description = (description or "").strip()
    for alias in aliases:
        text = description if (alias.field == "description" or not payee) else payee
        if text and alias.regex.search(text):
            return alias.normalized
    if payee:
        return clean_payee(payee)
    return clean_payee(clean_description(description)) if description else ""
