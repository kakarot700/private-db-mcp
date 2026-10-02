"""Explicit, JSON-compatible tool response contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Column(Model):
    name: str
    affinity: str
    nullable: bool
    primary_key_position: int
    masked: bool


class ForeignKey(Model):
    column: str
    referenced_table: str
    referenced_column: str | None


class Table(Model):
    name: str
    columns: list[Column]
    foreign_keys: list[ForeignKey]


class SchemaData(Model):
    tables: list[Table]
    fingerprint: str
    mermaid: str


class QueryData(Model):
    columns: list[str]
    rows: list[list[JsonValue]]
    row_count: int
    effective_limit: int
    truncated: bool
    masked_columns: list[str]


class ColumnStatistics(Model):
    column: str
    masked: bool
    null_count: int | None


class StatisticsData(Model):
    table: str
    row_count: int
    columns: list[ColumnStatistics]


class VerificationData(Model):
    matches: bool
    actual_fingerprint: str


class Error(Model):
    code: Literal[
        "INVALID_INPUT",
        "QUERY_REJECTED",
        "QUERY_FAILED",
        "RESOURCE_LIMIT",
        "DATABASE_UNAVAILABLE",
        "SCHEMA_MISMATCH",
        "POLICY_MISMATCH",
        "INTERNAL_ERROR",
    ]
    message: str


class Result[T](Model):
    ok: bool
    data: T | None = None
    error: Error | None = None
