"""Metadata discovery and bounded, policy-redacted snapshots.

Caller SQL NEVER executes against the source connection. Protected cells become
SQL NULL *before* projection/filtering/joining/aggregation on the query connection.
"""

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager

from .config import SENSITIVE, Settings, identifier
from .models import Column, ForeignKey, SchemaData, Table
from .security import Budget, SafeError, authorizer, harden


def quoted(name: str) -> str:
    # Only identifiers from the validated owner policy / discovered schema.
    identifier(name)
    return f'"{name}"'


def affinity(declaration: str) -> str:
    declaration = declaration.upper()
    if "INT" in declaration:
        return "INTEGER"
    if any(part in declaration for part in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if not declaration or "BLOB" in declaration:
        return "BLOB"
    if any(part in declaration for part in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


@contextmanager
def source(settings: Settings, budget: Budget) -> Iterator[sqlite3.Connection]:
    try:
        # as_uri percent-encodes ?, # and other URI-significant path bytes.
        conn = sqlite3.connect(
            settings.db_path.as_uri() + "?mode=ro",
            uri=True,
            timeout=0.2,
            isolation_level=None,
        )
    except sqlite3.Error:
        raise SafeError("DATABASE_UNAVAILABLE") from None
    with closing(conn):
        harden(conn, budget)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        yield conn
        # close() ends the read transaction, with no COMMIT or file writes.


def discover(conn: sqlite3.Connection, settings: Settings) -> SchemaData:
    # No sqlite_schema.sql, defaults, CHECK expressions, index SQL, or triggers
    # are returned: those can contain literal secrets.
    entries = conn.execute("PRAGMA main.table_list").fetchmany(4097)
    if len(entries) > 4096:
        raise SafeError("RESOURCE_LIMIT")
    table_types = {row[1]: row[2] for row in entries if row[0] == "main"}
    tables: list[Table] = []
    for name, rule in sorted(settings.policy.tables.items()):
        if table_types.get(name) != "table":
            raise SafeError("POLICY_MISMATCH")
        rows = conn.execute(f"PRAGMA main.table_xinfo({quoted(name)})").fetchmany(129)
        if not rows or len(rows) > 128 or any(row[6] for row in rows):
            raise SafeError("POLICY_MISMATCH")
        names = {row[1] for row in rows}
        if not set(rule.public_columns) <= names:
            raise SafeError("POLICY_MISMATCH")
        columns = []
        for _, col, decl, notnull, _default, pk, _hidden in rows:
            try:
                identifier(col)
            except ValueError:
                raise SafeError("POLICY_MISMATCH") from None
            columns.append(
                Column(
                    name=col,
                    affinity=affinity(decl),
                    nullable=not bool(notnull),
                    primary_key_position=pk,
                    masked=col not in rule.public_columns or bool(SENSITIVE.search(col)),
                )
            )
        keys = []
        for key in conn.execute(f"PRAGMA main.foreign_key_list({quoted(name)})"):
            _, _, parent, child_col, parent_col, *_ = key
            if parent in settings.policy.tables:
                try:
                    identifier(child_col)
                    if parent_col is not None:
                        identifier(parent_col)
                except ValueError:
                    raise SafeError("POLICY_MISMATCH") from None
                keys.append(
                    ForeignKey(
                        column=child_col,
                        referenced_table=parent,
                        referenced_column=parent_col,
                    )
                )
        tables.append(Table(name=name, columns=columns, foreign_keys=keys))
    canonical = json.dumps([t.model_dump() for t in tables], sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    if settings.expected_fingerprint and settings.expected_fingerprint != fingerprint:
        raise SafeError("SCHEMA_MISMATCH")
    lines = ["erDiagram"]
    for table in tables:
        lines.append(f"  {table.name} {{")
        for col in table.columns:
            key = " PK" if col.primary_key_position else ""
            note = ' "masked"' if col.masked else ""
            lines.append(f"    {col.affinity} {col.name}{key}{note}")
        lines.append("  }")
    for table in tables:
        for key in table.foreign_keys:
            # Relationship existence only; cardinality is deliberately not inferred.
            lines.append(f'  {table.name} }}o..o| {key.referenced_table} : "{key.column}"')
    return SchemaData(tables=tables, fingerprint=fingerprint, mermaid="\n".join(lines))


def safe_cell(value: object, settings: Settings) -> str | int | float | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        # BLOBs may hide credentials; even explicit allowlisting cannot disclose them.
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str) and len(value.encode("utf-8")) > settings.max_cell_bytes:
        raise SafeError("RESOURCE_LIMIT")
    if not isinstance(value, str | int | float):
        raise SafeError("QUERY_FAILED")
    return value


@contextmanager
def snapshot(settings: Settings, budget: Budget) -> Iterator[tuple[sqlite3.Connection, SchemaData]]:
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as clean:
        harden(clean, budget)
        clean.execute("PRAGMA max_page_count = 20000")
        total_rows = 0
        total_bytes = 0
        with source(settings, budget) as raw:
            schema = discover(raw, settings)
            public = {t.name: {c.name for c in t.columns if not c.masked} for t in schema.tables}
            raw.set_authorizer(authorizer(public, functions=False))
            for table in schema.tables:
                budget.check()
                # Deliberately no source defaults, constraints, triggers, or generated SQL.
                declarations = ", ".join(quoted(c.name) for c in table.columns)
                clean.execute(f"CREATE TABLE {quoted(table.name)} ({declarations})")
                projections = ", ".join(
                    f"NULL AS {quoted(c.name)}" if c.masked else quoted(c.name)
                    for c in table.columns
                )
                cursor = raw.execute(
                    f"SELECT {projections} FROM {quoted(table.name)} LIMIT ?",
                    (settings.snapshot_max_rows + 1,),
                )
                insert = (
                    f"INSERT INTO {quoted(table.name)} VALUES "
                    f"({','.join('?' for _ in table.columns)})"
                )
                sanitized_rows = []
                for row in cursor:
                    budget.check()
                    total_rows += 1
                    if total_rows > settings.snapshot_max_rows:
                        raise SafeError("RESOURCE_LIMIT")
                    values = [safe_cell(cell, settings) for cell in row]
                    total_bytes += len(json.dumps(values, ensure_ascii=True).encode())
                    if total_bytes > settings.snapshot_max_bytes:
                        raise SafeError("RESOURCE_LIMIT")
                    sanitized_rows.append(values)
                # A source covering index / WITHOUT ROWID key may be private.
                # Reorder only after redaction so implicit snapshot rowids and
                # unordered LIMIT queries cannot disclose private index order.
                sanitized_rows.sort(key=lambda row: json.dumps(row, ensure_ascii=True))
                for values in sanitized_rows:
                    budget.check()
                    clean.execute(insert, values)
        clean.execute("PRAGMA query_only = ON")
        clean.set_authorizer(
            authorizer(
                {t.name: {c.name for c in t.columns} for t in schema.tables},
                functions=True,
            )
        )
        budget.check()
        yield clean, schema
