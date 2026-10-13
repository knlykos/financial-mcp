"""Transaction categories and the rules that assign them.

Precedence for each transaction: manual choice > internal transfer > rules (lowest priority number first).
Generic seed categories/rules live here; personal ones (people's names, local shops) in local_rules.json.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from .pg import Connection, Row, executemany

KINDS = ("income", "expense", "transfer", "debt")
MATCH_FIELDS = ("payee_normalized", "payee", "description", "memo")
MATCH_TYPES = ("exact", "contains", "regex")
DIRECTIONS = ("any", "in", "out")
INTERNAL_TRANSFER = "Transferencia interna"

# Priorities: user rules 10, local_rules.json 50, seeds 100. Lower runs first.
USER_PRIORITY, LOCAL_PRIORITY, SEED_PRIORITY = 10, 50, 100

SEED_CATEGORIES: list[tuple[str, str]] = [
    ("Nómina", "income"),
    ("Uber (ingreso)", "income"),
    ("Otros ingresos", "income"),
    ("Súper", "expense"),
    ("Gasolina", "expense"),
    ("Comida fuera", "expense"),
    ("Préstamo auto", "debt"),
    ("Seguro auto", "expense"),
    ("Mantenimiento auto", "expense"),
    ("Pago de deuda", "debt"),
    ("Tarjetas de crédito", "debt"),
    ("Suscripciones", "expense"),
    ("Servicios", "expense"),
    ("Gobierno/DMV", "expense"),
    ("Transporte", "expense"),
    ("Salud", "expense"),
    ("Comisiones bancarias", "expense"),
    ("Intereses", "expense"),
    ("Envíos de dinero", "expense"),
    ("Efectivo/ATM", "expense"),
    ("Compras", "expense"),
    ("Hospedaje", "expense"),
    (INTERNAL_TRANSFER, "transfer"),
    ("Otros", "expense"),
]


def _exact(category: str, *names: str, direction: str = "any") -> list[tuple[str, str, str, str, str]]:
    return [("payee_normalized", "exact", n, category, direction) for n in names]


def _words(category: str, *words: str, direction: str = "any", field: str = "payee_normalized"):
    return [(field, "contains", w, category, direction) for w in words]


# (match_field, match_type, pattern, category, direction). "contains" matches whole words/phrases only,
# so "Mobil" never matches "MOBILE PMT".
SEED_RULES: list[tuple[str, str, str, str, str]] = [
    *_words("Nómina", "Payroll", direction="in"),
    *_exact("Uber (ingreso)", "Uber Payout"),
    # Loan payments say "Auto Loan" ("NISSAN RET Auto Loan"); a bare "Nissan" is also the dealer.
    *_exact("Préstamo auto", "Capital One Auto Loan"),
    *_words("Préstamo auto", "Auto Loan"),
    *_words("Préstamo auto", "Auto Loan", field="description", direction="out"),
    *_exact("Seguro auto", "Progressive", "Geico", "State Farm", "Allstate"),
    *_exact("Mantenimiento auto", "AutoZone", "O'Reilly Auto Parts", "Jiffy Lube", "Pep Boys", "Firestone"),
    *_words("Mantenimiento auto", "Auto Repair", "Auto Parts", "Tire", "Tires", "Oil Change"),
    *_words("Pago de deuda", "Consolidated Credit Solutions", "Oportun", "Sunbit"),
    # Payments to cards not connected to SimpleFIN. Payments to connected cards become internal transfers;
    # the card side ("in") is left uncategorized if its checking side is missing, rather than counted twice.
    *_exact("Tarjetas de crédito", "Capital One Credit Card", "Apple Credit Card", direction="out"),
    *_exact("Intereses", "Interest", "Interest Charge", direction="out"),
    ("description", "regex", r"PAYPAL\s+INST\s+XFER.*\bPPCR\b", "Tarjetas de crédito", "out"),
    *_exact("Servicios", "Cox Communications", "San Diego Gas & Electric", "AT&T", "Verizon", "T-Mobile"),
    *_exact(
        "Suscripciones", "Apple", "Spotify", "Microsoft", "Cloudflare", "Anthropic", "Luv Car Wash", "YouTube",
        "Netflix", "Rocket Money Premium", "Amazon Prime",
    ),
    *_words("Comisiones bancarias", "Fee", direction="out"),
    *_exact(
        "Gasolina", "Costco Gas", "ARCO", "Stars Gas", "Shell", "Chevron", "Mobil", "Pilot Flying J", "7-Eleven",
        "3rd Ave Sinclair",
    ),
    *_words("Gasolina", "Gasoline", "Fuel"),
    *_exact(
        "Súper", "Costco", "Walmart", "Carnival Supermarket", "Northgate Gonzalez Market", "Target", "El Super",
        "Vons", "Trader Joe's", "Smart & Final", "Grocery Outlet", "Pavillions", "Calimax", "Otay Farms Market",
        "Whole Foods", "Aldi",
    ),
    *_words("Súper", "Supermarket", "Grocery", "Market"),
    *_exact(
        "Comida fuera", "McDonald's", "In-N-Out Burger", "Starbucks", "Carl's Jr.", "Del Taco", "Domino's",
        "Jack in the Box", "Little Caesars", "Raising Cane's", "Shake Shack", "Wendy's",
    ),
    ("description", "regex", r"\bMC ?DONALD ?'?S\b", "Comida fuera", "out"),  # Capital One: "MC DONALD S ..."
    *_words("Comida fuera", "Taco", "Tacos", "Taco Shop", "Coffee", "Cafe", "Bakery", "Donut", "Pizza", "Burger"),
    *_exact("Gobierno/DMV", "California Department of Motor Vehicles"),
    *_words("Gobierno/DMV", "DMV"),
    *_exact("Transporte", "Uber", "Lyft", "San Diego Association of Governments", "Easy Park", "Pronto"),
    *_words("Transporte", "Parking"),
    *_exact("Salud", "CVS", "Walgreens", "Rady Children's Hospital"),
    *_words("Salud", "Pharmacy", "Hospital", "Medical", "Clinic"),
    *_exact("Envíos de dinero", "Remitly", "Transfer to Apple Pay"),
    *_exact("Envíos de dinero", "Zelle Transfer", direction="out"),
    *_exact("Otros ingresos", "Zelle Transfer", "Mobile Deposit", "Cash Deposit", "Cashback", direction="in"),
    *_exact("Efectivo/ATM", "ATM Withdrawal"),
    *_words("Hospedaje", "Hotel", "Motel", "Airbnb"),
    *_exact(
        "Compras", "Amazon", "Shein", "Ikea", "The Home Depot", "Burlington", "Michaels", "MINISO", "UPS", "Steam",
        "Humble Bundle", "TJ Maxx", "Five Below", "Harbor Freight Tools", "Guitar Center", "iFixit",
    ),
    # PayPal checkouts SimpleFIN couldn't name ("PAYPAL PURCHASE 260301 SOMESHOPXYZ ..."). Last, so a known
    # merchant behind PayPal (Steam, AutoZone) keeps its own category.
    ("description", "regex", r"^PAYPAL\s+PURCHASE\b", "Compras", "out"),
]


@dataclass(frozen=True)
class Rule:
    id: int
    field: str
    regex: re.Pattern[str]
    direction: str
    category_id: int

    def matches(self, tx: Row) -> bool:
        if self.direction == "in" and tx["amount"] <= 0 or self.direction == "out" and tx["amount"] >= 0:
            return False
        return bool(self.regex.search(tx[self.field] or ""))


def compile_pattern(match_type: str, pattern: str) -> re.Pattern[str]:
    if match_type == "exact":
        return re.compile(r"^\s*" + re.escape(pattern.strip()) + r"\s*$", re.I)
    if match_type == "contains":
        return re.compile(r"(?<!\w)" + re.escape(pattern.strip()) + r"(?!\w)", re.I)
    if match_type == "regex":
        try:
            return re.compile(pattern, re.I)
        except re.error as e:
            raise ToolError(f"Invalid regex {pattern!r}: {e}") from e
    raise ToolError(f"match_type must be one of {MATCH_TYPES}")


def _validate_rule(match_field: str, match_type: str, pattern: str, direction: str) -> re.Pattern[str]:
    if match_field not in MATCH_FIELDS:
        raise ToolError(f"match_field must be one of {MATCH_FIELDS}")
    if direction not in DIRECTIONS:
        raise ToolError(f"direction must be one of {DIRECTIONS}")
    if not pattern.strip():
        raise ToolError("pattern cannot be empty")
    return compile_pattern(match_type, pattern)


# --- Seeding --------------------------------------------------------------------------------------


def sync_seed_rules(conn: Connection, local: dict[str, Any]) -> bool:
    """Make seed/local categories and rules in the DB match the code and local_rules.json. Returns True if changed."""
    categories = [(n, k, "seed") for n, k in SEED_CATEGORIES]
    categories += [(c["name"], c.get("kind", "expense"), "local") for c in local.get("categories", [])]
    for name, kind, _ in categories:
        if kind not in KINDS:
            raise ValueError(f"Category {name!r} has invalid kind {kind!r}")
    executemany(
        conn,
        "INSERT INTO categories (name, kind, source) VALUES (%s, %s, %s) "
        "ON CONFLICT (name) DO UPDATE SET kind = excluded.kind WHERE categories.kind <> excluded.kind",
        categories,
    )

    wanted = [(*r, SEED_PRIORITY, "seed") for r in SEED_RULES]
    wanted += [
        (
            r.get("match_field", "payee_normalized"),
            r.get("match_type", "exact"),
            r["pattern"],
            r["category"],
            r.get("direction", "any"),
            r.get("priority", LOCAL_PRIORITY),
            "local",
        )
        for r in local.get("category_rules", [])
    ]
    ids = category_ids(conn)
    rows = []
    for field, mtype, pattern, category, direction, priority, source in wanted:
        _validate_rule(field, mtype, pattern, direction)
        if category.lower() not in ids:
            raise ValueError(f"Rule {pattern!r} points to unknown category {category!r}")
        rows.append((field, mtype, pattern, ids[category.lower()], direction, priority, source))

    snapshot = lambda: conn.execute(
        "SELECT match_field, match_type, pattern, category_id, direction, priority, source FROM category_rules "
        "WHERE source IN ('seed', 'local') ORDER BY id"
    ).fetchall()
    before = [tuple(r) for r in snapshot()]
    if [tuple(r) for r in rows] == before:
        return False
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM category_rules WHERE source IN ('seed', 'local')")
    executemany(
        conn,
        "INSERT INTO category_rules (match_field, match_type, pattern, category_id, direction, priority, source, "
        "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [(*r, now) for r in rows],
    )
    return True


# --- Lookups ---------------------------------------------------------------------------------------


def category_ids(conn: Connection) -> dict[str, int]:
    return {r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM categories")}


def resolve_category(conn: Connection, category: str | int) -> int:
    ids = category_ids(conn)
    if isinstance(category, int) or str(category).isdigit():
        if int(category) in ids.values():
            return int(category)
    elif str(category).lower() in ids:
        return ids[str(category).lower()]
    names = [r["name"] for r in conn.execute("SELECT name FROM categories ORDER BY name")]
    raise ToolError(f"Unknown category {category!r}. Valid categories: {names}")


def load_rules(conn: Connection) -> list[Rule]:
    rows = conn.execute("SELECT * FROM category_rules ORDER BY priority, id").fetchall()
    return [Rule(r["id"], r["match_field"], compile_pattern(r["match_type"], r["pattern"]), r["direction"],
                 r["category_id"]) for r in rows]


# --- Applying rules --------------------------------------------------------------------------------


def categorize(tx: Row, rules: list[Rule], internal_id: int) -> tuple[int | None, str, int | None]:
    """(category_id, category_source, id of the rule that matched)."""
    if tx["is_internal_transfer"]:
        return internal_id, "rule", None
    for rule in rules:
        if rule.matches(tx):
            return rule.category_id, "rule", rule.id
    return None, "none", None


_UPDATE_CATEGORY = (
    "UPDATE transactions SET category_id = %s, category_source = %s, category_rule_id = %s "
    "WHERE account_id = %s AND id = %s"
)


# Pending card charges cut the merchant name to ~12 characters ("Sunrise Mark" for "Sunrise Market").
TRUNCATED_LEN = 12


def same_merchant(a: str, b: str) -> bool:
    """Lowercased names: equal, or one is the other cut short (pending charges, "Microsoft" vs "Microsoft Re")."""
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    if long.startswith(short) and (len(short) >= 10 or len(short) >= 4 and long[len(short)] == " "):
        return True
    return len(short) >= TRUNCATED_LEN and short[:TRUNCATED_LEN] == long[:TRUNCATED_LEN]


class History:
    """What past transactions say about each merchant, per direction (money in vs out).

    Learns from manual choices and from rules that matched the merchant name (not the description: "NISSAN RET
    Auto Loan" says nothing about other "Nissan" charges). A merchant seen with more than one category is
    ambiguous and never guessed.
    """

    def __init__(self, seen: list[tuple[str, float, int]]):  # (payee_normalized, amount, category_id)
        self._cats: dict[tuple[bool, str], set[int]] = {}
        for name, amount, cat in seen:
            if key := name.strip().lower():
                self._cats.setdefault((amount < 0, key), set()).add(cat)

    def guess(self, name: str, amount: float) -> int | None:
        key, out = name.strip().lower(), amount < 0
        if not key:
            return None
        found: set[int] = set()
        for (known_out, known), cats in self._cats.items():
            if known_out == out and same_merchant(key, known):
                found |= cats
        return found.pop() if len(found) == 1 else None


def recategorize(conn: Connection, since: date | None = None) -> dict[str, int]:
    """Re-apply rules, then history, to every non-manual transaction (on or after `since`)."""
    rules = load_rules(conn)
    by_name = {r.id for r in rules if r.field in ("payee_normalized", "payee")}
    internal_id = resolve_category(conn, INTERNAL_TRANSFER)
    txs = conn.execute("SELECT * FROM transactions").fetchall()
    auto = [tx for tx in txs if tx["category_source"] != "manual"]
    results = {(tx["account_id"], tx["id"]): categorize(tx, rules, internal_id) for tx in auto}

    history = History(
        [(tx["payee_normalized"], tx["amount"], tx["category_id"])
         for tx in txs if tx["category_source"] == "manual" and not tx["is_internal_transfer"]]
        + [(tx["payee_normalized"], tx["amount"], cat)
           for tx in auto for cat, _, rule_id in [results[(tx["account_id"], tx["id"])]] if rule_id in by_name]
    )
    for tx in auto:
        key = (tx["account_id"], tx["id"])
        if results[key][0] is None and (guess := history.guess(tx["payee_normalized"], tx["amount"])):
            results[key] = (guess, "history", None)

    in_scope = [tx for tx in auto if since is None or str(tx["date"]) >= since.isoformat()]
    new = [(tx, results[(tx["account_id"], tx["id"])]) for tx in in_scope]
    updates = [
        (*cat, tx["account_id"], tx["id"])
        for tx, cat in new
        if cat != (tx["category_id"], tx["category_source"], tx["category_rule_id"])
    ]
    executemany(conn, _UPDATE_CATEGORY, updates)
    return {
        "evaluated": len(in_scope),
        "changed": len(updates),
        "uncategorized": sum(1 for _, (cat, _, _) in new if cat is None),
        "guessed_from_history": sum(1 for _, (_, source, _) in new if source == "history"),
    }


# --- Operations behind the MCP tools ---------------------------------------------------------------


def list_categories(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.kind, c.parent, COUNT(t.id) AS transactions,
               ROUND(COALESCE(SUM(t.amount), 0)::numeric, 2)::float8 AS total
        FROM categories c LEFT JOIN transactions t ON t.category_id = c.id
        GROUP BY c.id ORDER BY c.kind, c.name
        """
    ).fetchall()
    result = [dict(r) for r in rows]
    none = conn.execute(
        "SELECT COUNT(*), ROUND(COALESCE(SUM(amount), 0)::numeric, 2)::float8 FROM transactions WHERE category_id IS NULL"
    ).fetchone()
    result.append({"id": None, "name": "(sin categoría)", "kind": None, "parent": None,
                   "transactions": none[0], "total": none[1]})
    return result


def set_category(
    conn: Connection, transaction_ids: list[str], category: str | None, remember: bool = False
) -> dict[str, Any]:
    """Set a category by hand (wins over rules). category=None clears the manual choice and re-applies rules.

    remember=True also adds a user rule per merchant (exact payee_normalized and direction), so future charges,
    including the pending ones that are replaced when they post, get the category without a manual choice.
    """
    txs = conn.execute("SELECT * FROM transactions WHERE id = ANY(%s)", (transaction_ids,)).fetchall()
    found = {tx["id"] for tx in txs}
    if missing := [t for t in transaction_ids if t not in found]:
        raise ToolError(f"Unknown transaction ids: {missing}")
    if category is None:
        conn.execute("UPDATE transactions SET category_source = 'none' WHERE id = ANY(%s)", (transaction_ids,))
        recategorize(conn)
        return {"updated": len(found), "category": None, "note": "manual category cleared; rules re-applied"}

    cat_id = resolve_category(conn, category)
    rules = _remember(conn, txs, cat_id) if remember else []
    if rules:
        recategorize(conn)
    # Whatever the new rules didn't settle (all of them without remember) becomes a manual choice.
    settled = " AND (category_id IS DISTINCT FROM %s OR category_source <> 'rule')" if rules else ""
    manual = conn.execute(
        "UPDATE transactions SET category_id = %s, category_source = 'manual', category_rule_id = NULL "
        "WHERE id = ANY(%s)" + settled,
        (cat_id, transaction_ids, cat_id) if rules else (cat_id, transaction_ids),
    ).rowcount
    name = conn.execute("SELECT name FROM categories WHERE id = %s", (cat_id,)).fetchone()[0]
    return {"updated": len(found), "category": name, "rules_created": rules, "set_manually": max(manual, 0)}


def _remember(conn: Connection, txs: list[Row], cat_id: int) -> list[dict[str, str]]:
    """One user rule per (merchant, direction), replacing an existing user rule for the same merchant."""
    merchants = sorted(
        {(tx["payee_normalized"].strip(), "out" if tx["amount"] < 0 else "in") for tx in txs
         if tx["payee_normalized"].strip() and not tx["is_internal_transfer"]}
    )
    now = datetime.now().isoformat(timespec="seconds")
    for name, direction in merchants:
        conn.execute(
            "DELETE FROM category_rules WHERE source = 'user' AND match_field = 'payee_normalized' "
            "AND match_type = 'exact' AND lower(pattern) = lower(%s) AND direction = %s",
            (name, direction),
        )
        conn.execute(
            "INSERT INTO category_rules (match_field, match_type, pattern, category_id, direction, priority, source, "
            "created_at) VALUES ('payee_normalized', 'exact', %s, %s, %s, %s, 'user', %s)",
            (name, cat_id, direction, USER_PRIORITY, now),
        )
    return [{"merchant": name, "direction": direction} for name, direction in merchants]


def review_uncategorized(conn: Connection, include_guessed: bool = False, limit: int = 50) -> dict[str, Any]:
    """Uncategorized transactions (and optionally history guesses) grouped by merchant and direction."""
    rows = conn.execute(
        """
        SELECT t.id, t.date, t.amount, t.description, t.payee_normalized, t.pending, t.category_source,
               a.name AS account, c.name AS category
        FROM transactions t JOIN accounts a ON a.id = t.account_id LEFT JOIN categories c ON c.id = t.category_id
        WHERE NOT t.is_internal_transfer AND (t.category_id IS NULL OR (%s AND t.category_source = 'history'))
        ORDER BY t.date DESC
        """,
        (include_guessed,),
    ).fetchall()
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        direction = "out" if r["amount"] < 0 else "in"
        g = groups.setdefault((r["payee_normalized"], direction), {
            "merchant": r["payee_normalized"], "direction": direction, "count": 0, "total": 0.0,
            "guessed_category": r["category"] if r["category_source"] == "history" else None,
            "transaction_ids": [], "examples": [],
        })
        g["count"] += 1
        g["total"] = round(g["total"] + r["amount"], 2)
        g["transaction_ids"].append(r["id"])
        if len(g["examples"]) < 3:
            g["examples"].append({k: r[k] for k in ("date", "amount", "description", "account", "pending")})
    ordered = sorted(groups.values(), key=lambda g: -abs(g["total"]))
    by_kind: dict[str, list[str]] = {}
    for c in conn.execute("SELECT name, kind FROM categories ORDER BY kind, name"):
        by_kind.setdefault(c["kind"], []).append(c["name"])
    return {"groups": ordered[:limit], "more_groups": max(len(ordered) - limit, 0), "categories": by_kind}


def list_rules(conn: Connection) -> list[dict[str, Any]]:
    """Rules in the order they run; `transactions` = how many this rule categorized (not its whole category)."""
    rows = conn.execute(
        """
        SELECT r.id, r.match_field, r.match_type, r.pattern, r.direction, c.name AS category, r.priority, r.source,
               COUNT(t.id) AS transactions
        FROM category_rules r JOIN categories c ON c.id = r.category_id
        LEFT JOIN transactions t ON t.category_rule_id = r.id
        GROUP BY r.id, c.name ORDER BY r.priority, r.id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def preview_rule(
    conn: Connection, pattern: str, match_field: str = "payee_normalized", match_type: str = "exact",
    direction: str = "any", limit: int = 50,
) -> dict[str, Any]:
    """Preview which transactions a rule would match, without saving anything."""
    regex = _validate_rule(match_field, match_type, pattern, direction)
    rule = Rule(0, match_field, regex, direction, 0)
    rows = conn.execute(
        """
        SELECT t.*, c.name AS current_category FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
        ORDER BY t.date DESC
        """
    ).fetchall()
    hits = [r for r in rows if rule.matches(r)]
    return {
        "matches": len(hits),
        "manual_ones_unaffected": sum(1 for r in hits if r["category_source"] == "manual"),
        "total": round(sum(r["amount"] for r in hits), 2),
        "sample": [
            {"id": r["id"], "date": r["date"], "amount": round(r["amount"], 2), "payee_normalized": r["payee_normalized"],
             "current_category": r["current_category"]}
            for r in hits[:limit]
        ],
    }


def add_rule(
    conn: Connection, pattern: str, category: str, match_field: str = "payee_normalized",
    match_type: str = "exact", direction: str = "any", priority: int = USER_PRIORITY,
) -> dict[str, Any]:
    _validate_rule(match_field, match_type, pattern, direction)
    cat_id = resolve_category(conn, category)
    rule_id = conn.execute(
        "INSERT INTO category_rules (match_field, match_type, pattern, category_id, direction, priority, source, "
        "created_at) VALUES (%s, %s, %s, %s, %s, %s, 'user', %s) RETURNING id",
        (match_field, match_type, pattern, cat_id, direction, priority, datetime.now().isoformat(timespec="seconds")),
    ).fetchone()[0]
    return {"rule_id": rule_id, **recategorize(conn)}


def delete_rule(conn: Connection, rule_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT source FROM category_rules WHERE id = %s", (rule_id,)).fetchone()
    if not row:
        raise ToolError(f"No rule with id {rule_id}")
    if row["source"] != "user":
        raise ToolError(
            f"Rule {rule_id} is a {row['source']} rule. Override it with a user rule (lower priority number wins) "
            "or edit local_rules.json."
        )
    conn.execute("DELETE FROM category_rules WHERE id = %s", (rule_id,))
    return {"deleted": rule_id, **recategorize(conn)}
