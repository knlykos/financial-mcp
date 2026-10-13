"""Small psycopg helpers shared by the modules that query the database."""

from collections.abc import Iterable, Sequence
from typing import Any

import psycopg
from psycopg.types.string import TextLoader

Connection = psycopg.Connection["Row"]


class Row(tuple):
    """A result row readable by position or column name (like sqlite3.Row); dict(row) works too."""

    _index: dict[str, int] = {}

    def __getitem__(self, key: Any) -> Any:
        return super().__getitem__(self._index[key] if isinstance(key, str) else key)

    def keys(self) -> list[str]:
        return list(self._index)


def row_factory(cursor: psycopg.Cursor[Any]):
    names = [c.name for c in cursor.description or []]
    return type("Row", (Row,), {"_index": {n: i for i, n in enumerate(names)}})


def connect(url: str) -> Connection:
    conn = psycopg.connect(url, row_factory=row_factory, connect_timeout=20)
    # Dates and timestamps come back as ISO text, the shape the tools have always returned.
    for type_name in ("date", "timestamp"):
        conn.adapters.register_loader(type_name, TextLoader)
    return conn


def executemany(conn: Connection, sql: str, rows: Iterable[Sequence[Any]]) -> int:
    """Run `sql` once per row (pipelined, so one round trip); returns the total rows affected."""
    with conn.cursor() as cur:
        cur.executemany(sql, list(rows))
        return max(cur.rowcount, 0)
