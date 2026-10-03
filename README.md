# personal-financial-mcp

Servidor MCP que lee tus cuentas bancarias (Wells Fargo, etc.) a través de [SimpleFIN Bridge](https://beta-bridge.simplefin.org/) y guarda las transacciones en una base SQLite local para analizarlas. Solo lectura.

## Setup

```sh
uv sync
uv run pfm-setup <tu-setup-token-de-simplefin>
```

El setup token es de un solo uso: `pfm-setup` lo canjea por un *access URL* permanente y lo guarda en `.simplefin-access-url`. Si ya tienes el access URL (`https://usuario:clave@...`), pásalo en lugar del token o ponlo en la variable `SIMPLEFIN_ACCESS_URL`.

## Registrar en Claude Code

```sh
claude mcp add finanzas -- uv run --directory C:/Users/nefil/Documents/dev/personal-financial-mcp personal-financial-mcp
```

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `list_accounts` | Cuentas con saldo actual y disponible (`refresh=true` para pedirlos al banco). |
| `sync_transactions` | Descarga hasta 90 días de transacciones a la base local. |
| `get_transactions` | Consulta la base local por fechas, cuenta o texto. |

SimpleFIN solo da ~90 días de historial por consulta, actualiza los datos ~1 vez al día y limita las consultas diarias. Por eso todo se guarda en `data/finance.db`: sincronizando regularmente, el historial crece más allá de esos 90 días.

## Estructura

- `src/personal_financial_mcp/simplefin.py`: cliente del protocolo SimpleFIN.
- `src/personal_financial_mcp/db.py`: esquema SQLite y consultas.
- `src/personal_financial_mcp/server.py`: herramientas MCP. Las nuevas funciones de análisis van aquí.
- `src/personal_financial_mcp/setup.py`: canje del setup token.

`.simplefin-access-url` y `data/` están en `.gitignore`: contienen tus credenciales y tus datos financieros.
