# Remote deployment

The same server, over streamable HTTP at `https://<FINANZAS_DOMAIN>/mcp`, behind OAuth 2.1
(see `src/personal_financial_mcp/auth.py`), plus the read-only web panel (`web/`) at `https://<PANEL_DOMAIN>`.
Runs with Docker Compose: the MCP container, the panel container and their own Caddy, which handles HTTPS
(Let's Encrypt) and is the only thing exposed.

```
Claude (web/mobile/Desktop/Code) ──HTTPS──▶ Caddy :443 ──▶ finanzas-mcp:8000 ──▶ Neon
Browser ─────────────────────────HTTPS──▶ Caddy :443 ──▶ finanzas-web:3100 ──▶ Neon (+ Neon Auth)
```

## Authentication

- **OAuth** (claude.ai, Desktop, Claude Code): the client registers itself, opens `/login`, and you sign in with
  your password + authenticator code. Access and refresh tokens last 30 days (refresh rotated on use; claude.ai
  doesn't reliably refresh, so a short access token meant signing in again every hour);
  both are stored in Postgres as SHA-256 hashes. Five failed logins lock the form for 15 minutes.
- **Static token** (`FINANZAS_API_TOKEN`): `Authorization: Bearer <token>`, for scripts or clients without OAuth.
- **Web panel**: Neon Auth (email + password), limited to `ALLOWED_EMAILS`. See `web/README.md`.

## Server layout (`~/finanzas-mcp`)

| File | What |
|---|---|
| `.env` (600) | `FINANZAS_DOMAIN`, `PANEL_DOMAIN`, `DATABASE_URL`, `SIMPLEFIN_ACCESS_URL`, and the `pfm-auth init` output |
| `web.env` (600) | the panel's settings (`web/README.md`), with its own `NEON_AUTH_COOKIE_SECRET` |
| `local_rules.json` | personal rules, mounted read-only |
| `compose.yml`, `Caddyfile`, `Dockerfile`, `src/`, `web/`... | copied from this repo |

## First-time setup

1. DNS: `A` records for both domains pointing at the server; ports 80 and 443 open (cloud firewall too).
2. Locally: `uv run python -c "from personal_financial_mcp.auth import cli; cli()" init` → writes `auth.env`
   (password hash, TOTP secret, API token) after confirming a code from your authenticator app.
3. Append it to the server's `.env` (`ssh <server> 'cat >> finanzas-mcp/.env' < auth.env`), then delete `auth.env`.
4. Write `web.env` (see `web/README.md`) with `chmod 600`.
5. `cd ~/finanzas-mcp && sudo docker compose up -d --build`.

## Update

```sh
tar --force-local --exclude=__pycache__ --exclude=web/node_modules --exclude=web/.env -czf build.tgz \
  pyproject.toml uv.lock README.md src Dockerfile .dockerignore web -C deploy compose.yml Caddyfile
scp build.tgz <server>:/tmp/ && rm build.tgz
ssh <server> 'cd ~/finanzas-mcp && tar -xzf /tmp/build.tgz && sudo docker compose up -d --build'
```

Extract with the default umask: the containers run as unprivileged users and must be able to read the code
(a `umask 077` in the same shell made both containers crash-loop with `EACCES`).

## Operations

- Logs: `sudo docker logs -f finanzas-mcp` (and `finanzas-web`, `finanzas-caddy`).
- Daily sync (root crontab, 14:00 UTC ≈ 7 am Pacific): `docker exec finanzas-mcp pfm-sync`, output in
  `/var/log/finanzas-sync.log`.
- Sign everyone out: `DELETE FROM oauth_tokens;` (and rotate `FINANZAS_API_TOKEN` if it leaked).
- Change password or authenticator: run `pfm-auth hash-password` / `pfm-auth new-totp`, edit `.env`,
  `sudo docker compose up -d`.
