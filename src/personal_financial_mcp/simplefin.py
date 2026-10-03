"""Minimal client for the SimpleFIN protocol (https://www.simplefin.org/protocol.html)."""

import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx
from mcp.server.mcpserver.exceptions import ToolError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACCESS_URL_FILE = PROJECT_ROOT / ".simplefin-access-url"


class SimpleFINError(ToolError):
    """Raised for expected failures; MCP reports the message to the client."""


def claim_setup_token(setup_token: str) -> str:
    """Exchange a one-time setup token for a permanent access URL."""
    try:
        claim_url = base64.b64decode(setup_token.strip()).decode()
    except ValueError as e:
        raise SimpleFINError("The setup token is not valid base64.") from e
    if not claim_url.startswith("https://"):
        raise SimpleFINError("The setup token did not decode to an https URL.")

    res = httpx.post(claim_url, timeout=30)
    if res.status_code != 200:
        raise SimpleFINError(
            f"Claiming the setup token failed (HTTP {res.status_code}). Setup tokens can only be claimed once."
        )
    return res.text.strip()


def save_access_url(access_url: str) -> None:
    ACCESS_URL_FILE.write_text(access_url + "\n")


def load_access_url() -> str:
    if url := os.environ.get("SIMPLEFIN_ACCESS_URL", "").strip():
        return url
    if ACCESS_URL_FILE.exists():
        return ACCESS_URL_FILE.read_text().strip()
    raise SimpleFINError("No SimpleFIN access URL configured. Run `uv run pfm-setup <setup-token>` first.")


def fetch_accounts(
    start: datetime | None = None,
    end: datetime | None = None,
    pending: bool = False,
    balances_only: bool = False,
    account_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch the account set. Returns the raw SimpleFIN JSON ({"errors": [...], "accounts": [...]})."""
    parts = urlsplit(load_access_url())
    auth = (unquote(parts.username or ""), unquote(parts.password or ""))
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    url = urlunsplit((parts.scheme, host, parts.path.rstrip("/") + "/accounts", "", ""))

    params: list[tuple[str, str]] = []
    if start:
        params.append(("start-date", str(int(start.timestamp()))))
    if end:
        params.append(("end-date", str(int(end.timestamp()))))
    if pending:
        params.append(("pending", "1"))
    if balances_only:
        params.append(("balances-only", "1"))
    params += [("account", a) for a in account_ids or []]

    res = httpx.get(url, params=params, auth=auth, timeout=60)
    if res.status_code == 403:
        raise SimpleFINError("SimpleFIN rejected the credentials (HTTP 403). The access URL may have been revoked.")
    if res.status_code != 200:
        raise SimpleFINError(f"SimpleFIN request failed (HTTP {res.status_code}): {res.text}")
    return res.json()
