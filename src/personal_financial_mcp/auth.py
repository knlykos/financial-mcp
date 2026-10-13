"""OAuth 2.1 for the remote (HTTP) server: a single-user authorization server built on the MCP SDK.

The SDK serves the standard endpoints (metadata, dynamic client registration, /authorize, /token, /revoke).
This module supplies the parts it leaves to us: who the user is (a login page with password + TOTP) and
where clients and tokens live (Postgres, hashed, so a restart doesn't log anyone out).

A static API token (FINANZAS_API_TOKEN) is also accepted, for clients that can send a header but don't do
OAuth, such as Claude Code with `--header`.

Configuration (environment):
  FINANZAS_PUBLIC_URL     https://finanzas.example.com (issuer and resource URL)
  FINANZAS_PASSWORD_HASH  from `pfm-auth hash-password`
  FINANZAS_TOTP_SECRET    base32, from `pfm-auth new-totp`
  FINANZAS_API_TOKEN      optional static bearer token, from `pfm-auth new-token`
"""

import base64
import hashlib
import hmac
import html
import os
import secrets
import struct
import time
from typing import Any
from urllib.parse import urlencode

import anyio
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import db

SCOPE = "finanzas"
SUBJECT = "owner"
# claude.ai doesn't always use the refresh token: when the access token expired it registered a new client and
# asked to sign in again. A long-lived access token avoids that; tokens are stored hashed and can be revoked.
ACCESS_TTL = 30 * 24 * 60 * 60  # 30 days
REFRESH_TTL = 30 * 24 * 60 * 60  # 30 days
CODE_TTL = 5 * 60
LOGIN_TTL = 10 * 60
# Lock the login form after this many failures within the window (there is only one user to guess).
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set; see `pfm-auth --help`.")
    return value


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- Password (scrypt, stdlib) and TOTP (RFC 6238) ------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024)
    # ':' separators, not '$': Docker Compose interpolates '$' in env files and would mangle the hash.
    return f"scrypt:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.strip("'\"").replace("$", ":").split(":")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024)
    return hmac.compare_digest(digest.hex(), digest_hex)


def totp(secret_b32: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**digits
    return f"{code:0{digits}d}"


def verify_totp(code: str, secret_b32: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    code = code.strip().replace(" ", "")
    # Accept the previous and next 30s window too, for clock drift.
    return any(hmac.compare_digest(totp(secret_b32, now + drift * 30), code) for drift in (-1, 0, 1))


# --- Provider -------------------------------------------------------------------------------------


class FinanzasOAuthProvider:
    """OAuthAuthorizationServerProvider for one user. Pending logins and codes are short-lived and in memory."""

    def __init__(self, public_url: str) -> None:
        self.public_url = public_url.rstrip("/")
        self._pending: dict[str, tuple[float, OAuthClientInformationFull, AuthorizationParams]] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._failures: list[float] = []

    # Postgres access is sync (psycopg); keep it off the event loop.
    @staticmethod
    async def _db(fn, *args: Any) -> Any:
        def run() -> Any:
            with db.connect() as conn:
                return fn(conn, *args)

        return await anyio.to_thread.run_sync(run)

    # Clients (dynamic registration) -------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        def q(conn):
            row = conn.execute("SELECT info FROM oauth_clients WHERE client_id = %s", (client_id,)).fetchone()
            return row and row["info"]

        info = await self._db(q)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        def q(conn):
            conn.execute(
                "INSERT INTO oauth_clients (client_id, info) VALUES (%s, %s) "
                "ON CONFLICT (client_id) DO UPDATE SET info = EXCLUDED.info",
                (client_info.client_id, client_info.model_dump_json()),
            )

        await self._db(q)

    # Authorization: hand off to our login page ---------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self._expire()
        request_id = secrets.token_urlsafe(24)
        self._pending[request_id] = (time.time() + LOGIN_TTL, client, params)
        return f"{self.public_url}/login?{urlencode({'request': request_id})}"

    def _expire(self) -> None:
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v[0] > now}
        self._codes = {k: v for k, v in self._codes.items() if v.expires_at > now}
        self._failures = [t for t in self._failures if t > now - FAILURE_WINDOW]

    def locked(self) -> bool:
        self._expire()
        return len(self._failures) >= MAX_FAILURES

    async def login_page(self, request: Request) -> Response:
        request_id = request.query_params.get("request", "") or str((await request.form()).get("request", ""))
        pending = self._pending.get(request_id)
        if not pending or pending[0] < time.time():
            return _page("La solicitud expiró. Vuelve a conectar desde Claude.", status=400)
        _, client, params = pending
        target = html.escape(f"{client.client_name or 'Cliente'} → {params.redirect_uri.host}")

        if request.method == "GET":
            return _login_form(request_id, target)

        if self.locked():
            return _page("Demasiados intentos fallidos. Espera 15 minutos.", status=429)
        form = await request.form()
        ok_password = await anyio.to_thread.run_sync(
            verify_password, str(form.get("password", "")), _env("FINANZAS_PASSWORD_HASH")
        )
        ok_totp = verify_totp(str(form.get("code", "")), _env("FINANZAS_TOTP_SECRET"))
        if not (ok_password and ok_totp):
            self._failures.append(time.time())
            return _login_form(request_id, target, error="Contraseña o código incorrectos.")

        del self._pending[request_id]
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=time.time() + CODE_TTL,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=SUBJECT,
        )
        return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state), 302)

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        self._expire()
        code = self._codes.get(authorization_code)
        return code if code and code.client_id == client.client_id else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self._codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "authorization code already used")
        return await self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    # Tokens ---------------------------------------------------------------------------------------

    async def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        now = int(time.time())

        def q(conn):
            conn.execute(
                "INSERT INTO oauth_tokens (token_hash, kind, client_id, scopes, resource, expires_at) "
                "VALUES (%s, 'access', %s, %s, %s, %s), (%s, 'refresh', %s, %s, %s, %s)",
                (
                    _sha256(access), client_id, " ".join(scopes), resource, now + ACCESS_TTL,
                    _sha256(refresh), client_id, " ".join(scopes), resource, now + REFRESH_TTL,
                ),
            )
            conn.execute("DELETE FROM oauth_tokens WHERE expires_at < %s", (now,))

        await self._db(q)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    async def _load(self, token: str, kind: str) -> dict[str, Any] | None:
        def q(conn):
            row = conn.execute(
                "SELECT client_id, scopes, resource, expires_at FROM oauth_tokens "
                "WHERE token_hash = %s AND kind = %s AND expires_at > %s",
                (_sha256(token), kind, int(time.time())),
            ).fetchone()
            return dict(row) if row else None

        return await self._db(q)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = await self._load(refresh_token, "refresh")
        if not row or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"].split(),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=SUBJECT,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        # Rotate: the old refresh token stops working once it's used.
        await self._delete(refresh_token.token)
        return await self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        static = os.environ.get("FINANZAS_API_TOKEN", "").strip()
        if static and hmac.compare_digest(token, static):
            return AccessToken(token=token, client_id="api-token", scopes=[SCOPE], subject=SUBJECT)
        row = await self._load(token, "access")
        if not row:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"].split(),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=SUBJECT,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._delete(token.token)

    async def _delete(self, token: str) -> None:
        await self._db(lambda conn: conn.execute("DELETE FROM oauth_tokens WHERE token_hash = %s", (_sha256(token),)))

    async def exchange_identity_assertion(self, client, params) -> OAuthToken:
        raise TokenError("unsupported_grant_type", "identity assertions are not supported")


def settings(public_url: str) -> AuthSettings:
    return AuthSettings(
        issuer_url=public_url,
        resource_server_url=f"{public_url.rstrip('/')}/mcp",
        validate_token_resource=False,  # one resource per issuer; tokens can't be used anywhere else
        required_scopes=[SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
        ),
        revocation_options=RevocationOptions(enabled=True),
    )


# --- HTML -----------------------------------------------------------------------------------------

_STYLE = """
body{font-family:system-ui,sans-serif;background:#f4f4f5;color:#18181b;display:flex;justify-content:center;
padding:48px 16px;margin:0}
main{background:#fff;border-radius:12px;padding:28px;max-width:360px;width:100%;box-shadow:0 1px 3px #0002}
h1{font-size:20px;margin:0 0 4px}p{color:#52525b;font-size:14px}label{display:block;font-size:14px;margin:14px 0 4px}
input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #d4d4d8;border-radius:8px;font-size:16px}
button{margin-top:20px;width:100%;padding:11px;border:0;border-radius:8px;background:#18181b;color:#fff;font-size:16px}
.err{color:#b91c1c}
@media (prefers-color-scheme:dark){body{background:#18181b;color:#f4f4f5}main{background:#27272a}
p{color:#a1a1aa}input{background:#18181b;color:#f4f4f5;border-color:#3f3f46}button{background:#f4f4f5;color:#18181b}}
"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=es><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'><title>Finanzas</title>"
        f"<link rel=icon href=/icon.svg type=image/svg+xml><link rel=apple-touch-icon href=/apple-touch-icon.png>"
        f"<style>{_STYLE}</style></head><body><main>{body}</main></body></html>",
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"},
    )


def _login_form(request_id: str, target: str, error: str = "") -> HTMLResponse:
    err = f"<p class=err>{html.escape(error)}</p>" if error else ""
    return _page(
        f"<h1>Finanzas</h1><p>Autorizar acceso a: <b>{target}</b></p>{err}"
        f"<form method=post action=/login><input type=hidden name=request value='{html.escape(request_id)}'>"
        f"<label for=p>Contraseña</label><input id=p name=password type=password autocomplete=current-password required autofocus>"
        f"<label for=c>Código del autenticador</label>"
        f"<input id=c name=code inputmode=numeric autocomplete=one-time-code pattern='[0-9 ]{{6,7}}' required>"
        f"<button>Autorizar</button></form>",
        status=200 if not error else 401,
    )


# --- CLI: pfm-auth --------------------------------------------------------------------------------


def _ask_password() -> str:
    import getpass

    password = getpass.getpass("Password (12+ characters): ")
    if len(password) < 12:
        raise SystemExit("Use at least 12 characters.")
    if password != getpass.getpass("Repeat: "):
        raise SystemExit("Passwords don't match.")
    return password


def _new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _otpauth_uri(secret: str) -> str:
    from urllib.parse import quote

    return f"otpauth://totp/{quote('Finanzas MCP')}?secret={secret}&issuer={quote('Finanzas MCP')}"


def cli() -> None:
    """Generate the secrets for the remote server's environment file."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="pfm-auth", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="interactive: password + authenticator + API token, written to an env file")
    init.add_argument("--out", default="auth.env", help="env file to write (default: auth.env)")
    sub.add_parser("hash-password", help="prompt for a password and print FINANZAS_PASSWORD_HASH")
    sub.add_parser("new-totp", help="print a new FINANZAS_TOTP_SECRET and its otpauth:// URI")
    sub.add_parser("new-token", help="print a new random FINANZAS_API_TOKEN")
    args = parser.parse_args()

    if args.command == "init":
        out = Path(args.out)
        if out.exists():
            raise SystemExit(f"{out} already exists; delete it first to start over.")
        password_hash = hash_password(_ask_password())
        secret = _new_totp_secret()
        print("\nAdd this account to your authenticator app (Google Authenticator, 1Password, Authy...):")
        print("  Setup key:", " ".join(secret[i : i + 4] for i in range(0, len(secret), 4)))
        print("  Or URI:   ", _otpauth_uri(secret))
        for _ in range(3):
            if verify_totp(input("\nType the 6-digit code it shows now: "), secret):
                break
            print("That code doesn't match; check the key and the phone's clock.")
        else:
            raise SystemExit("Authenticator not confirmed; nothing written.")
        token = secrets.token_urlsafe(32)
        out.write_text(
            f"FINANZAS_PASSWORD_HASH={password_hash}\nFINANZAS_TOTP_SECRET={secret}\nFINANZAS_API_TOKEN={token}\n"
        )
        out.chmod(0o600)
        print(f"\nWrote {out.resolve()}. Keep it private; it goes into the server's .env.")
    elif args.command == "hash-password":
        print(f"FINANZAS_PASSWORD_HASH={hash_password(_ask_password())}")
    elif args.command == "new-totp":
        secret = _new_totp_secret()
        print(f"FINANZAS_TOTP_SECRET={secret}")
        print(_otpauth_uri(secret))
    else:
        print(f"FINANZAS_API_TOKEN={secrets.token_urlsafe(32)}")
