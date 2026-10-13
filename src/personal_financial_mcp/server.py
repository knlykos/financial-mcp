import os
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import Icon
from pydantic import Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import auth, categories, db
from .transfers import match_internal_transfers
from .simplefin import fetch_accounts

TRANSACTION_FIELDS = [
    "date", "amount", "description", "payee", "payee_normalized", "memo", "pending", "account", "account_id", "id",
    "is_internal_transfer", "transfer_pair_id", "category", "category_source",
]

# Set on the remote deployment (HTTP + OAuth); unset when run locally over stdio.
PUBLIC_URL = os.environ.get("FINANZAS_PUBLIC_URL", "").strip().rstrip("/")
oauth = auth.FinanzasOAuthProvider(PUBLIC_URL) if PUBLIC_URL else None
# Icon shown by clients (the claude.ai connector list); served by the routes below.
STATIC = Path(__file__).parent / "static"
ICONS = {
    "/icon.png": ("icon.png", "image/png"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}

mcp = MCPServer(
    name="personal-financial-mcp",
    instructions=(
        "Personal finance data from the user's bank accounts (via SimpleFIN), stored in its own Postgres database. "
        "Call sync_transactions before analysis if data looks stale; SimpleFIN data refreshes about once a day "
        "and allows only a limited number of requests per day, so don't sync repeatedly.\n\n"
        "You are the one who categorizes. Rules and the merchant's past categories handle the known merchants; "
        "when sync_transactions or list_categories show uncategorized transactions, call review_uncategorized and "
        "categorize each group you can identify from the merchant name and raw bank description with "
        "set_category(remember=true), so the same merchant is automatic from then on. Ask the user about groups "
        "you can't identify (people's names, unknown shops) instead of guessing. Generic payees (PayPal, Zelle "
        "Transfer, Online Transfer) cover many merchants or people: categorize those without remember, or add a "
        "description rule with manage_rules."
    ),
    auth_server_provider=oauth,
    auth=auth.settings(PUBLIC_URL) if PUBLIC_URL else None,
    icons=[
        Icon(src=f"{PUBLIC_URL}/icon.png", mime_type="image/png", sizes=["512x512"]),
        Icon(src=f"{PUBLIC_URL}/icon.svg", mime_type="image/svg+xml", sizes=["any"]),
    ] if PUBLIC_URL else None,
)

if oauth:

    @mcp.custom_route("/login", methods=["GET", "POST"], include_in_schema=False)
    async def login(request: Request) -> Response:
        return await oauth.login_page(request)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def icon(request: Request) -> Response:
        name, media_type = ICONS[request.url.path]
        return FileResponse(STATIC / name, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})

    for path in ICONS:
        mcp.custom_route(path, methods=["GET"], include_in_schema=False)(icon)


@mcp.tool()
def list_accounts(
    refresh: Annotated[bool, Field(description="Fetch fresh balances from the bank instead of the stored copy.")] = False,
) -> list[dict[str, Any]]:
    """List connected bank accounts (e.g. Wells Fargo) with current and available balances."""
    with db.connect() as conn:
        if refresh or not conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
            db.upsert_accounts(conn, fetch_accounts(balances_only=True)["accounts"])
            db.fix_balance_signs(conn)
        return [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY institution, name")]


@mcp.tool()
def sync_transactions(
    days: Annotated[int, Field(ge=1, le=90, description="How many days back to download.")] = 90,
) -> dict[str, Any]:
    """Download recent transactions and balances from the bank into the database."""
    data = fetch_accounts(start=datetime.now() - timedelta(days=days), pending=True)
    with db.connect() as conn:
        db.upsert_accounts(conn, data["accounts"])
        changed = db.upsert_transactions(conn, data["accounts"])
        sign_fixed = db.fix_balance_signs(conn)  # needs the fresh pending rows
        pairs = match_internal_transfers(conn)
        cats = categories.recategorize(conn)
        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return {
        "accounts": len(data["accounts"]),
        "downloaded": sum(len(a.get("transactions", [])) for a in data["accounts"]),
        "rows_written": changed,
        "internal_transfer_pairs": pairs,
        "uncategorized": cats["uncategorized"],
        "guessed_from_history": cats["guessed_from_history"],
        **({"next_step": "call review_uncategorized and categorize them"} if cats["uncategorized"] else {}),
        "total_stored": total,
        "balance_sign_fixed": sign_fixed,
        "simplefin_errors": data.get("errors", []),
    }


@mcp.tool()
def get_transactions(
    start_date: Annotated[date | None, Field(description="Inclusive start (YYYY-MM-DD). Default: 30 days ago.")] = None,
    end_date: Annotated[date | None, Field(description="Inclusive end (YYYY-MM-DD). Default: today.")] = None,
    account_ids: Annotated[list[str] | None, Field(description="Only these account ids (from list_accounts).")] = None,
    search: Annotated[str | None, Field(description="Case-insensitive text match on description, payee or memo.")] = None,
    include_pending: bool = True,
    limit: Annotated[int, Field(ge=1, le=5000)] = 500,
    fields: Annotated[
        list[str] | None,
        Field(description=f"Only return these fields, e.g. ['date','amount','payee_normalized']. Valid: {TRANSACTION_FIELDS}."),
    ] = None,
    compact: Annotated[bool, Field(description="Omit the long raw description and memo fields.")] = False,
    exclude_internal: Annotated[
        bool, Field(description="Leave out transfers between the user's own accounts (not real income/spending).")
    ] = False,
    category: Annotated[
        str | None,
        Field(description="Only this category (name from list_categories). Use '(sin categoría)' for uncategorized."),
    ] = None,
) -> dict[str, Any]:
    """Query stored transactions. Amounts are negative for money out, positive for money in.

    Reads from the database; run sync_transactions first if it is empty or out of date.
    payee_normalized is a cleaned merchant name, better for grouping than payee or description.
    Use fields and/or compact to keep responses small when listing many transactions.
    is_internal_transfer marks money moved between the user's own accounts; transfer_pair_id links both sides.
    category_source is 'manual' (set by hand), 'rule', 'history' (guessed from the merchant's past category)
    or 'none'.
    """
    if unknown := set(fields or []) - set(TRANSACTION_FIELDS):
        raise ToolError(f"Unknown fields {sorted(unknown)}. Valid fields: {TRANSACTION_FIELDS}")
    end = end_date or date.today()
    start = start_date or end - timedelta(days=30)
    with db.connect() as conn:
        uncategorized = category is not None and category.strip().lower() in ("(sin categoría)", "sin categoría", "none")
        category_id = categories.resolve_category(conn, category) if category and not uncategorized else None
        rows = db.query_transactions(
            conn, start, end, account_ids, search, include_pending, limit, exclude_internal=exclude_internal,
            category_id=category_id, uncategorized=uncategorized,
        )
        last_sync = conn.execute("SELECT MAX(synced_at) FROM accounts").fetchone()[0]

    keep = [f for f in (fields or TRANSACTION_FIELDS) if not (compact and f in ("description", "memo"))]
    if keep != TRANSACTION_FIELDS:
        rows = [{k: r[k] for k in keep} for r in rows]
    return {"count": len(rows), "last_synced": last_sync, "transactions": rows}


@mcp.tool()
def list_categories() -> list[dict[str, Any]]:
    """List categories with their kind (income, expense, transfer, debt), transaction count and total amount.

    The last row counts transactions without a category.
    """
    with db.connect() as conn:
        return categories.list_categories(conn)


@mcp.tool()
def set_category(
    transaction_ids: Annotated[list[str], Field(min_length=1, description="Transaction ids (the 'id' field).")],
    category: Annotated[
        str | None, Field(description="Category name from list_categories. null clears the manual choice.")
    ],
    remember: Annotated[
        bool,
        Field(description="Also create a rule for each merchant (payee_normalized + direction) so its future "
                          "transactions get this category automatically. Not for generic payees like PayPal."),
    ] = False,
) -> dict[str, Any]:
    """Set the category of transactions. Manual categories always win over rules.

    Pending transactions are replaced when they post, so a category set on a pending one without remember=true
    is lost at the next sync.
    """
    with db.connect() as conn:
        return categories.set_category(conn, transaction_ids, category, remember)


@mcp.tool()
def review_uncategorized(
    include_guessed: Annotated[
        bool, Field(description="Also include groups categorized by history guess, to confirm or correct them.")
    ] = False,
    limit: Annotated[int, Field(ge=1, le=200, description="Max merchant groups, largest totals first.")] = 50,
) -> dict[str, Any]:
    """Transactions without a category, grouped by merchant and direction, for you to categorize.

    Each group has the merchant name, count, total, transaction_ids and up to 3 examples with the raw bank
    description (often names the shop, city or Zelle note). `categories` lists valid names by kind. For each
    group you can identify, call set_category(transaction_ids, category, remember=true).
    """
    with db.connect() as conn:
        return categories.review_uncategorized(conn, include_guessed, limit)


@mcp.tool()
def manage_rules(
    action: Annotated[
        Literal["list", "add", "delete", "test"],
        Field(description="list rules; add a rule; delete a user rule; test = preview matches without saving."),
    ],
    pattern: Annotated[str | None, Field(description="Text to match (add/test).")] = None,
    category: Annotated[str | None, Field(description="Category name for add.")] = None,
    match_field: Literal["payee_normalized", "payee", "description", "memo"] = "payee_normalized",
    match_type: Annotated[
        Literal["exact", "contains", "regex"],
        Field(description="exact = whole field; contains = whole words/phrase only; regex = case-insensitive regex."),
    ] = "exact",
    direction: Annotated[
        Literal["any", "in", "out"], Field(description="Only money in, only money out, or both.")
    ] = "any",
    priority: Annotated[int, Field(description="Lower runs first. User rules default to 10; seeds are 100.")] = 10,
    rule_id: Annotated[int | None, Field(description="Rule to delete.")] = None,
) -> Any:
    """Manage the rules that categorize transactions. Adding or deleting a rule re-applies all rules
    (manual categories are never touched). Try a rule with action='test' before adding it."""
    with db.connect() as conn:
        if action == "list":
            return categories.list_rules(conn)
        if action == "delete":
            if rule_id is None:
                raise ToolError("rule_id is required to delete")
            return categories.delete_rule(conn, rule_id)
        if not pattern:
            raise ToolError(f"pattern is required to {action}")
        if action == "test":
            return categories.preview_rule(conn, pattern, match_field, match_type, direction)
        if not category:
            raise ToolError("category is required to add a rule")
        return categories.add_rule(conn, pattern, category, match_field, match_type, direction, priority)


@mcp.tool()
def recategorize(
    since: Annotated[date | None, Field(description="Only transactions on or after this date. Default: all.")] = None,
) -> dict[str, Any]:
    """Re-apply the categorization rules and history guesses. Manual categories are never changed."""
    with db.connect() as conn:
        return categories.recategorize(conn, since)


def http_app():
    """The remote server: streamable HTTP at /mcp behind OAuth, only answering to FINANZAS_PUBLIC_URL's host."""
    for name in ("FINANZAS_PASSWORD_HASH", "FINANZAS_TOTP_SECRET"):
        auth._env(name)  # fail at startup, not at the first login
    return mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[PUBLIC_URL.split("://", 1)[1]],
            allowed_origins=[PUBLIC_URL],
        ),
        host="0.0.0.0",
    )


def main() -> None:
    """stdio by default; with FINANZAS_PUBLIC_URL set, the HTTP server (see http_app)."""
    if not PUBLIC_URL:
        mcp.run()
        return
    import uvicorn

    # Behind Caddy: trust its X-Forwarded-* headers so logs show the real client IP.
    uvicorn.run(
        http_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


def sync_main() -> None:
    """`pfm-sync`: one sync_transactions run, for a daily cron job."""
    import json

    print(json.dumps(sync_transactions(days=14), default=str))
