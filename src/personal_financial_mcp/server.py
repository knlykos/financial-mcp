from datetime import date, datetime, timedelta
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import db
from .simplefin import fetch_accounts

TRANSACTION_FIELDS = [
    "date", "amount", "description", "payee", "payee_normalized", "memo", "pending", "account", "account_id", "id",
]

mcp = MCPServer(
    name="personal-financial-mcp",
    instructions=(
        "Personal finance data from the user's bank accounts (via SimpleFIN), cached in a local database. "
        "Call sync_transactions before analysis if data looks stale; SimpleFIN data refreshes about once a day "
        "and allows only a limited number of requests per day, so don't sync repeatedly."
    ),
)


@mcp.tool()
def list_accounts(
    refresh: Annotated[bool, Field(description="Fetch fresh balances from the bank instead of the local cache.")] = False,
) -> list[dict[str, Any]]:
    """List connected bank accounts (e.g. Wells Fargo) with current and available balances."""
    with db.connect() as conn:
        if refresh or not conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
            db.upsert_accounts(conn, fetch_accounts(balances_only=True)["accounts"])
        return [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY institution, name")]


@mcp.tool()
def sync_transactions(
    days: Annotated[int, Field(ge=1, le=90, description="How many days back to download.")] = 90,
) -> dict[str, Any]:
    """Download recent transactions and balances from the bank into the local database."""
    data = fetch_accounts(start=datetime.now() - timedelta(days=days), pending=True)
    with db.connect() as conn:
        db.upsert_accounts(conn, data["accounts"])
        changed = db.upsert_transactions(conn, data["accounts"])
        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return {
        "accounts": len(data["accounts"]),
        "downloaded": sum(len(a.get("transactions", [])) for a in data["accounts"]),
        "rows_written": changed,
        "total_stored": total,
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
) -> dict[str, Any]:
    """Query transactions stored locally. Amounts are negative for money out, positive for money in.

    Reads from the local database; run sync_transactions first if it is empty or out of date.
    payee_normalized is a cleaned merchant name, better for grouping than payee or description.
    Use fields and/or compact to keep responses small when listing many transactions.
    """
    if unknown := set(fields or []) - set(TRANSACTION_FIELDS):
        raise ToolError(f"Unknown fields {sorted(unknown)}. Valid fields: {TRANSACTION_FIELDS}")
    end = end_date or date.today()
    start = start_date or end - timedelta(days=30)
    with db.connect() as conn:
        rows = db.query_transactions(conn, start, end, account_ids, search, include_pending, limit)
        last_sync = conn.execute("SELECT MAX(synced_at) FROM accounts").fetchone()[0]

    keep = [f for f in (fields or TRANSACTION_FIELDS) if not (compact and f in ("description", "memo"))]
    if keep != TRANSACTION_FIELDS:
        rows = [{k: r[k] for k in keep} for r in rows]
    return {"count": len(rows), "last_synced": last_sync, "transactions": rows}


def main() -> None:
    mcp.run()
