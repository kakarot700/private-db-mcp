"""Independent AST gate and SQLite VM authorizers. Never interpolate caller SQL."""

import sqlite3
import time
from collections.abc import Callable

import sqlglot
from sqlglot import exp

from .config import Settings

MESSAGES = {
    "INVALID_INPUT": "Invalid input; check tool argument types and bounds.",
    "QUERY_REJECTED": "Only a single permitted read-only SELECT query is allowed.",
    "QUERY_FAILED": "Query could not be executed against the disclosed schema.",
    "RESOURCE_LIMIT": "Operation exceeded its configured resource budget.",
    "DATABASE_UNAVAILABLE": "Configured database is unavailable.",
    "SCHEMA_MISMATCH": "Schema does not match the owner-approved fingerprint.",
    "POLICY_MISMATCH": "Configured policy does not match a supported database schema.",
    "INTERNAL_ERROR": "Operation failed safely.",
}


class SafeError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])


def validate_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip() or len(query.encode("utf-8")) > 16384:
        raise SafeError("INVALID_INPUT")
    if "\x00" in query:
        raise SafeError("QUERY_REJECTED")
    try:
        statements = sqlglot.parse(query, read="sqlite")
        if len(statements) != 1 or not isinstance(
            statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)
        ):
            raise SafeError("QUERY_REJECTED")
        tree = statements[0]
        nodes = list(tree.walk())
        if len(nodes) > 1024:
            raise SafeError("RESOURCE_LIMIT")
        forbidden = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Create,
            exp.Drop,
            exp.Alter,
            exp.Command,
            exp.Into,
            exp.Lock,
            exp.Transaction,
            exp.Commit,
            exp.Rollback,
        )
        for node in nodes:
            if isinstance(node, forbidden):
                raise SafeError("QUERY_REJECTED")
            if isinstance(node, exp.With) and node.args.get("recursive"):
                raise SafeError("QUERY_REJECTED")
            if isinstance(node, exp.Table) and (
                node.db or node.catalog or not isinstance(node.this, exp.Identifier)
            ):
                raise SafeError("QUERY_REJECTED")
    except SafeError:
        raise
    except (sqlglot.errors.SqlglotError, RecursionError, ValueError, TypeError):
        raise SafeError("QUERY_REJECTED") from None
    # Execute the ORIGINAL statement, not a parser translation. The VM gate is authoritative.
    return query


SAFE_FUNCTIONS = frozenset(
    {
        "abs",
        "avg",
        "ceil",
        "ceiling",
        "coalesce",
        "count",
        "date",
        "datetime",
        "floor",
        "format",
        "glob",
        "group_concat",
        "hex",
        "ifnull",
        "instr",
        "julianday",
        "length",
        "like",
        "lower",
        "ltrim",
        "max",
        "min",
        "nullif",
        "printf",
        "quote",
        "replace",
        "round",
        "rtrim",
        "strftime",
        "substr",
        "substring",
        "sum",
        "time",
        "total",
        "trim",
        "typeof",
        "unicode",
        "unixepoch",
        "upper",
        "row_number",
        "rank",
        "dense_rank",
        "lag",
        "lead",
        "first_value",
        "last_value",
    }
)


def authorizer(
    tables: dict[str, set[str]], *, functions: bool
) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    allowed = {table.lower(): {c.lower() for c in cols} for table, cols in tables.items()}

    def authorize(
        action: int, arg1: str | None, arg2: str | None, database: str | None, trigger: str | None
    ) -> int:
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            cols = allowed.get((arg1 or "").lower())
            # SQLite reports database=None for the empty-column read used by
            # count(*) / SELECT constants. The final argument can also be a CTE
            # name, not only a trigger. Only clean snapshots accept CTE contexts;
            # these contain no source views or triggers.
            database_ok = database == "main" or (database is None and arg2 == "")
            if (
                database_ok
                and (not trigger or functions)
                and cols is not None
                and (not arg2 or arg2.lower() in cols)
            ):
                return sqlite3.SQLITE_OK
        if (
            action == sqlite3.SQLITE_FUNCTION
            and functions
            and (arg2 or "").lower() in SAFE_FUNCTIONS
        ):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorize


class Budget:
    def __init__(self, settings: Settings):
        self.deadline = time.monotonic() + settings.timeout_seconds
        self.max_steps = settings.max_vm_steps
        self.interval = min(100, settings.max_vm_steps)
        self.steps = 0
        self.exceeded = False

    def progress(self) -> int:
        self.steps += self.interval
        self.exceeded = self.steps > self.max_steps or time.monotonic() > self.deadline
        return int(self.exceeded)

    def check(self) -> None:
        if time.monotonic() > self.deadline or self.exceeded:
            raise SafeError("RESOURCE_LIMIT")


def harden(connection: sqlite3.Connection, budget: Budget) -> None:
    connection.enable_load_extension(False)
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 32768)
    connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 256)
    connection.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 64)
    connection.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 16)
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 128)
    connection.setlimit(sqlite3.SQLITE_LIMIT_VDBE_OP, 100_000)
    connection.set_progress_handler(budget.progress, budget.interval)
