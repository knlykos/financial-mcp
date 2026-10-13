"""The remote server's OAuth flow, end to end over HTTP (Starlette TestClient against the real app)."""

import base64
import hashlib
import importlib
import json
import re
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from personal_financial_mcp import auth, server

URL = "https://finanzas.test"
PASSWORD = "correct horse battery staple"
TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
API_TOKEN = "static-test-token"
REDIRECT = "https://claude.example/callback"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
}


@pytest.fixture(scope="module")
def password_hash() -> str:
    return auth.hash_password(PASSWORD)  # scrypt is slow on purpose; hash once


@pytest.fixture
def client(tmp_db, monkeypatch, password_hash):
    monkeypatch.setenv("FINANZAS_PUBLIC_URL", URL)
    monkeypatch.setenv("FINANZAS_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("FINANZAS_TOTP_SECRET", TOTP_SECRET)
    monkeypatch.setenv("FINANZAS_API_TOKEN", API_TOKEN)
    importlib.reload(server)  # the server reads FINANZAS_PUBLIC_URL at import
    try:
        with TestClient(server.http_app(), base_url=URL) as c:
            yield c
    finally:
        monkeypatch.delenv("FINANZAS_PUBLIC_URL")
        importlib.reload(server)


def register(client) -> dict:
    res = client.post(
        "/register",
        json={"redirect_uris": [REDIRECT], "client_name": "Claude", "token_endpoint_auth_method": "none"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def start_login(client, reg) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    res = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": reg["client_id"],
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
        follow_redirects=False,
    )
    assert res.status_code == 302, res.text
    login_url = res.headers["location"]
    assert login_url.startswith(f"{URL}/login?request=")
    return login_url, verifier


def submit(client, login_url, password=PASSWORD, code=None):
    request_id = parse_qs(urlparse(login_url).query)["request"][0]
    return client.post(
        "/login",
        data={"request": request_id, "password": password, "code": code or auth.totp(TOTP_SECRET)},
        follow_redirects=False,
    )


def login(client) -> tuple[dict, dict]:
    reg = register(client)
    login_url, verifier = start_login(client, reg)
    res = submit(client, login_url)
    assert res.status_code == 302, res.text
    query = parse_qs(urlparse(res.headers["location"]).query)
    assert query["state"] == ["xyz"]
    tokens = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": reg["client_id"],
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200, tokens.text
    return reg, tokens.json()


def call_mcp(client, token):
    headers = MCP_HEADERS | ({"Authorization": f"Bearer {token}"} if token else {})
    return client.post("/mcp", json=INITIALIZE, headers=headers)


def test_mcp_requires_a_token(client):
    res = call_mcp(client, None)
    assert res.status_code == 401
    assert "resource_metadata" in res.headers["www-authenticate"]
    assert call_mcp(client, "made-up").status_code == 401


def test_metadata_is_discoverable(client):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"].rstrip("/") == URL
    assert meta["registration_endpoint"] == f"{URL}/register"
    resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert resource["resource"] == f"{URL}/mcp"


def test_full_login_flow_gives_a_working_token(client):
    _, tokens = login(client)
    assert tokens["token_type"].lower() == "bearer"
    res = call_mcp(client, tokens["access_token"])
    assert res.status_code == 200, res.text
    assert "personal-financial-mcp" in res.text


def test_login_page_shows_who_is_asking(client):
    login_url, _ = start_login(client, register(client))
    page = client.get(login_url)
    assert page.status_code == 200
    assert "claude.example" in page.text


def test_wrong_password_or_code_is_rejected(client):
    login_url, _ = start_login(client, register(client))
    assert submit(client, login_url, password="nope").status_code == 401
    assert submit(client, login_url, code="000000").status_code == 401
    assert submit(client, login_url).status_code == 302  # still usable after a typo


def test_login_locks_after_repeated_failures(client):
    login_url, _ = start_login(client, register(client))
    for _ in range(auth.MAX_FAILURES):
        submit(client, login_url, password="nope")
    assert submit(client, login_url).status_code == 429  # even the right password, until the window passes


def test_authorization_code_works_once(client):
    reg = register(client)
    login_url, verifier = start_login(client, reg)
    code = parse_qs(urlparse(submit(client, login_url).headers["location"]).query)["code"][0]
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": reg["client_id"],
        "code_verifier": verifier,
    }
    assert client.post("/token", data=data).status_code == 200
    assert client.post("/token", data=data).status_code == 400


def test_refresh_rotates_and_old_refresh_token_dies(client):
    reg, tokens = login(client)
    data = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": reg["client_id"]}
    res = client.post("/token", data=data)
    assert res.status_code == 200, res.text
    assert call_mcp(client, res.json()["access_token"]).status_code == 200
    assert client.post("/token", data=data).status_code == 400


def test_revoked_token_stops_working(client):
    reg, tokens = login(client)
    # The SDK's revocation model requires client_secret to be present, even empty for public clients.
    data = {"token": tokens["access_token"], "client_id": reg["client_id"], "client_secret": ""}
    res = client.post("/revoke", data=data)
    assert res.status_code == 200, res.text
    assert call_mcp(client, tokens["access_token"]).status_code == 401


def test_tokens_are_stored_hashed(client):
    _, tokens = login(client)
    from personal_financial_mcp import db

    with db.connect() as conn:
        stored = {r["token_hash"] for r in conn.execute("SELECT token_hash FROM oauth_tokens")}
    assert tokens["access_token"] not in stored
    assert hashlib.sha256(tokens["access_token"].encode()).hexdigest() in stored


def test_static_api_token(client):
    assert call_mcp(client, API_TOKEN).status_code == 200


def test_icons_are_public_and_announced(client):
    for path, media_type in [("/icon.png", "image/png"), ("/icon.svg", "image/svg+xml"), ("/favicon.ico", "image/")]:
        res = client.get(path)
        assert res.status_code == 200 and res.headers["content-type"].startswith(media_type), path
    res = call_mcp(client, API_TOKEN)
    body = res.text if "{" in res.text[:1] else next(l[5:] for l in res.text.splitlines() if l.startswith("data:"))
    icons = json.loads(body)["result"]["serverInfo"]["icons"]
    assert {i["src"] for i in icons} == {f"{URL}/icon.png", f"{URL}/icon.svg"}


def test_other_host_is_rejected(client):
    res = client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS | {"Authorization": f"Bearer {API_TOKEN}", "Host": "evil.test"})
    assert res.status_code in (400, 421)


def test_totp_matches_rfc6238_vectors():
    # RFC 6238 appendix B, SHA-1 key "12345678901234567890".
    assert auth.totp(TOTP_SECRET, 59, digits=8) == "94287082"
    assert auth.totp(TOTP_SECRET, 1111111109, digits=8) == "07081804"
    assert re.fullmatch(r"\d{6}", auth.totp(TOTP_SECRET))


def test_password_hash_survives_env_files():
    stored = auth.hash_password(PASSWORD)
    assert "$" not in stored  # Docker Compose would interpolate it
    assert auth.verify_password(PASSWORD, stored)
    assert auth.verify_password(PASSWORD, stored.replace(":", "$"))  # hashes made before the switch
    assert not auth.verify_password("wrong", stored)
