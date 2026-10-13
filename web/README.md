# Panel web de Finanzas

Panel de solo lectura sobre la misma base de datos del servidor MCP: resumen del mes, gasto por categoría,
movimientos con filtros y las reglas de categorización con cuántos movimientos categorizó cada una.

**Stack:** [Hono](https://hono.dev) con JSX renderizado en el servidor sobre [Bun](https://bun.sh),
[htmx](https://htmx.org) para cargar fragmentos (filtros de movimientos, detalle de cada categoría) y
[Alpine.js](https://alpinejs.dev) para la interactividad local (login, menú, filtro de reglas). htmx y Alpine se
sirven desde `node_modules`, sin CDN.

## Autenticación (Neon Auth)

El login usa [Neon Auth](https://neon.com/docs/auth/overview) (Managed Better Auth) de la rama `main` del
proyecto de Neon, con correo y contraseña. El navegador solo habla con este servidor: `/api/auth/*` se reenvía a
Neon Auth con el toolkit `@neondatabase/auth/server`, así que las cookies de sesión son HttpOnly y de este dominio.

Neon Auth deja que cualquiera se registre, así que el panel agrega dos candados:

- `/api/auth/sign-up/email` solo acepta los correos de `ALLOWED_EMAILS`.
- Cada página exige una sesión **y** que el correo esté en `ALLOWED_EMAILS`; una cuenta creada directamente en
  Neon Auth no ve nada.

Después de crear tu cuenta, desactiva el registro en la consola de Neon (Auth → Email & password → Allow sign up).

Solo se reenvían `sign-in/email`, `sign-up/email`, `sign-out` y `get-session`.

## Base de datos

Se conecta como el rol `finanzas_web`, que solo tiene `SELECT` sobre `accounts`, `transactions`, `categories`,
`category_rules` y `payee_aliases` (nada de las tablas OAuth del MCP). Si una migración agrega tablas que el panel
deba leer, hay que darle `GRANT SELECT` a ese rol.

## Desarrollo

```sh
cd web
bun install
bun run dev        # http://localhost:3100, recarga al guardar
bun test           # pruebas
bun run typecheck
```

`web/.env` (no está en git):

| Variable | Qué es |
|---|---|
| `DATABASE_URL` | Postgres con el rol `finanzas_web` |
| `NEON_AUTH_BASE_URL` | URL de Neon Auth de la rama (consola de Neon → Auth) |
| `NEON_AUTH_COOKIE_SECRET` | 32+ caracteres aleatorios; firma la caché de sesión |
| `ALLOWED_EMAILS` | Correos con acceso, separados por coma |
| `PORT` | 3100 por defecto |

Los orígenes que Neon Auth acepta están en sus *trusted domains*: `https://<PANEL_DOMAIN>` y
`http://localhost:3100`.

## Producción

Corre como el servicio `web` de `deploy/compose.yml`, con Caddy sirviendo `PANEL_DOMAIN`. Sus variables van en
`web.env` junto al `.env` del servidor (con su propio `NEON_AUTH_COOKIE_SECRET`). Ver `deploy/README.md`.
