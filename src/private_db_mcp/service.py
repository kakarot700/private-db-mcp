"""Tool implementations with uniform safe failure behavior."""

import json
import sqlite3
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from .config import FINGERPRINT, Settings
from .database import discover, quoted, safe_cell, snapshot, source
from .models import (
    ColumnStatistics,
    Error,
    QueryData,
    Result,
    SchemaData,
    StatisticsData,
    VerificationData,
)
from .security import MESSAGES, Budget, SafeError, validate_query

T = TypeVar("T", bound=BaseModel)


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _run(self, operation: Callable[[Budget], T]) -> Result[T]:
        budget = Budget(self.settings)
        try:
            data = operation(budget)
            budget.check()
            if len(data.model_dump_json().encode()) > self.settings.max_response_bytes:
                raise SafeError("RESOURCE_LIMIT")
            return Result(ok=True, data=data)
        except SafeError as exc:
            code = exc.code
        except (sqlite3.Error, OverflowError, MemoryError):
            code = "RESOURCE_LIMIT" if budget.exceeded else "QUERY_FAILED"
        except Exception:
            # Never return native exceptions, paths, SQL, bindings, or tracebacks.
            code = "INTERNAL_ERROR"
        return Result(ok=False, error=Error(code=code, message=MESSAGES[code]))

    def inspect_schema(self) -> Result[SchemaData]:
        def operation(budget: Budget) -> SchemaData:
            with source(self.settings, budget) as conn:
                return discover(conn, self.settings)

        return self._run(operation)

    def verify_schema(self, expected_fingerprint: str) -> Result[VerificationData]:
        def operation(budget: Budget) -> VerificationData:
            if not FINGERPRINT.fullmatch(expected_fingerprint):
                raise SafeError("INVALID_INPUT")
            with source(self.settings, budget) as conn:
                schema = discover(conn, self.settings)
            return VerificationData(
                matches=schema.fingerprint == expected_fingerprint,
                actual_fingerprint=schema.fingerprint,
            )

        return self._run(operation)

    def execute_safe_query(
        self,
        query: str,
        parameters: list[str | int | float | None] | None = None,
        row_limit: int = 100,
    ) -> Result[QueryData]:
        def operation(budget: Budget) -> QueryData:
            sql = validate_query(query)
            if type(row_limit) is not int:
                raise SafeError("INVALID_INPUT")
            limit = max(1, min(row_limit, self.settings.max_rows))
            params = parameters if parameters is not None else []
            if not isinstance(params, list) or len(params) > 128:
                raise SafeError("INVALID_INPUT")
            for value in params:
                if value is not None and type(value) not in (str, int, float):
                    raise SafeError("INVALID_INPUT")
                safe_cell(value, self.settings)
            with snapshot(self.settings, budget) as (conn, schema):
                cursor = conn.execute(sql, params)
                rows = []
                size = 0
                truncated = False
                for index, row in enumerate(cursor):
                    budget.check()
                    if index == limit:
                        truncated = True
                        break
                    values = [safe_cell(cell, self.settings) for cell in row]
                    size += len(json.dumps(values, ensure_ascii=True).encode())
                    if size > self.settings.max_response_bytes:
                        raise SafeError("RESOURCE_LIMIT")
                    rows.append(values)
                return QueryData(
                    columns=[c[0] for c in cursor.description],
                    rows=rows,
                    row_count=len(rows),
                    effective_limit=limit,
                    truncated=truncated,
                    masked_columns=[
                        f"{t.name}.{c.name}" for t in schema.tables for c in t.columns if c.masked
                    ],
                )

        return self._run(operation)

    def get_table_statistics(self, table: str) -> Result[StatisticsData]:
        def operation(budget: Budget) -> StatisticsData:
            if table not in self.settings.policy.tables:
                raise SafeError("INVALID_INPUT")
            with snapshot(self.settings, budget) as (conn, schema):
                spec = next(t for t in schema.tables if t.name == table)
                count = conn.execute(f"SELECT count(*) FROM {quoted(table)}").fetchone()[0]
                stats = []
                for col in spec.columns:
                    null_count = None
                    if not col.masked:
                        null_count = conn.execute(
                            f"SELECT count(*) FROM {quoted(table)} WHERE {quoted(col.name)} IS NULL"
                        ).fetchone()[0]
                    stats.append(
                        ColumnStatistics(
                            column=col.name,
                            masked=col.masked,
                            null_count=null_count,
                        )
                    )
                return StatisticsData(table=table, row_count=count, columns=stats)

        return self._run(operation)
