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
| `get_transactions` | Consulta la base local por fechas, cuenta o texto. Parámetros opcionales `fields`, `compact` y `exclude_internal`. |

Cada transacción incluye `payee_normalized`: el nombre del comercio limpio (sin ciudad, prefijos ni referencias) y unificado con alias, ideal para agrupar. Ejemplos:

```jsonc
// Solo 3 campos: ~5x más pequeño que la respuesta completa
get_transactions({"start_date": "2026-07-01", "fields": ["date", "amount", "payee_normalized"]})
// Todo menos la descripción cruda y el memo
get_transactions({"compact": true, "search": "costco"})
```

### Transferencias internas

El dinero que mueves entre tus propias cuentas no es ingreso ni gasto. Después de cada sync se emparejan: una salida en una cuenta propia y una entrada del mismo monto en otra cuenta propia, con 2 días o menos de diferencia (si hay varios candidatos, gana la fecha más cercana). Ambas quedan con `is_internal_transfer: true` y el mismo `transfer_pair_id`.

Solo se consideran movimientos tipo transferencia (`TRANSFER`/`XFER`), y si la descripción menciona una cuenta (`XXXXXX1234`) que no es tuya, se trata como transferencia a un tercero. Las cuentas propias salen de la tabla `accounts`.

```jsonc
// Solo ingresos y gastos reales
get_transactions({"start_date": "2026-08-01", "end_date": "2026-08-31", "exclude_internal": true})
```

SimpleFIN solo da ~90 días de historial por consulta, actualiza los datos ~1 vez al día y limita las consultas diarias. Por eso todo se guarda en `data/finance.db`: sincronizando regularmente, el historial crece más allá de esos 90 días.

## Alias personales (`local_rules.json`)

Los alias genéricos (Uber, Apple, Costco...) viven en `normalize.py`. Los que contienen datos personales (tu empleador, tu nombre) van en `local_rules.json`, que no se sube a git:

```json
{
  "payee_aliases": [
    {"pattern": "^Acme Corp Payroll", "normalized": "Acme Payroll"},
    {"pattern": "ONLINE TRANSFER (TO|FROM) DOE J\\b", "normalized": "Transfer Doe J", "field": "description"}
  ]
}
```

`pattern` es una expresión regular sin distinguir mayúsculas. Por defecto se compara con el payee de SimpleFIN; con `"field": "description"` se compara con la descripción cruda del banco. Los alias locales tienen prioridad sobre los genéricos y, al cambiar el archivo, todo se renormaliza en el siguiente arranque.

## Base de datos y pruebas

- El esquema se migra solo al arrancar (`PRAGMA user_version`); antes de migrar se guarda una copia en `data/backups/`.
- Pruebas (solo datos inventados): `uv run pytest`

## Estructura

- `src/personal_financial_mcp/simplefin.py`: cliente del protocolo SimpleFIN.
- `src/personal_financial_mcp/db.py`: esquema, migraciones y consultas SQLite.
- `src/personal_financial_mcp/normalize.py`: limpieza de nombres de comercio y alias.
- `src/personal_financial_mcp/transfers.py`: emparejamiento de transferencias internas.
- `src/personal_financial_mcp/server.py`: herramientas MCP. Las nuevas funciones de análisis van aquí.
- `src/personal_financial_mcp/setup.py`: canje del setup token.

`.simplefin-access-url`, `local_rules.json` y `data/` están en `.gitignore`: contienen tus credenciales y tus datos financieros.
