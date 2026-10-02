import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from private_db_mcp.config import Policy, Settings, TablePolicy
from private_db_mcp.database import affinity, safe_cell
from private_db_mcp.demo import create_demo
from private_db_mcp.models import Result, SchemaData
from private_db_mcp.security import Budget, SafeError
from private_db_mcp.service import Service


def test_schema_discovery_relationships_and_mermaid(service):
    result = service.inspect_schema()
    assert result.ok, result
    schema = result.data
    assert {table.name for table in schema.tables} == {"customers", "transactions", "audit_logs"}
    transactions = next(t for t in schema.tables if t.name == "transactions")
    assert transactions.foreign_keys[0].referenced_table == "customers"
    assert transactions.foreign_keys[0].column == "customer_id"
    assert schema.mermaid.startswith("erDiagram\n")
    assert '"masked"' in schema.mermaid
    assert len(schema.fingerprint) == 64
    assert Result[SchemaData].model_validate_json(result.model_dump_json()) == result


def test_verify_schema_and_drift(service, db_path):
    fingerprint = service.inspect_schema().data.fingerprint
    assert service.verify_schema(fingerprint).data.matches
    assert not service.verify_schema("0" * 64).data.matches
    assert not service.verify_schema("bad").ok
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("ALTER TABLE customers ADD COLUMN newly_private TEXT")
        conn.commit()
    result = service.inspect_schema()
    assert result.ok
    assert result.data.fingerprint != fingerprint
    col = next(
        c
        for t in result.data.tables
        if t.name == "customers"
        for c in t.columns
        if c.name == "newly_private"
    )
    assert col.masked


def test_owner_pinned_schema_fails_closed(settings, db_path):
    original = Service(settings).inspect_schema().data.fingerprint
    pinned = Service(replace(settings, expected_fingerprint=original))
    assert pinned.inspect_schema().ok
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("ALTER TABLE customers ADD COLUMN unexpected TEXT")
    for result in [
        pinned.inspect_schema(),
        pinned.execute_safe_query("SELECT 1"),
        pinned.get_table_statistics("customers"),
        pinned.verify_schema(original),
    ]:
        assert not result.ok
        assert result.error.code == "SCHEMA_MISMATCH"


def test_statistics_do_not_disclose_masked_null_counts(service):
    result = service.get_table_statistics("customers")
    assert result.ok
    assert result.data.row_count == 12
    cols = {c.column: c for c in result.data.columns}
    assert cols["email"].masked and cols["email"].null_count is None
    assert not cols["id"].masked and cols["id"].null_count == 0


@pytest.mark.parametrize("table", ["internal_secrets", 'customers"; DROP TABLE customers;--', "x"])
def test_statistics_table_name_is_allowlisted(service, table):
    result = service.get_table_statistics(table)
    assert not result.ok and result.error.code == "INVALID_INPUT"


def test_allowlisting_sensitive_names_cannot_override_automatic_mask(settings):
    policy = Policy(
        tables={
            "customers": TablePolicy(
                public_columns=[
                    "id",
                    "email",
                    "full_name",
                    "password_hash",
                    "api_token",
                    "phone",
                ]
            )
        }
    )
    service = Service(replace(settings, policy=policy))
    result = service.execute_safe_query(
        "SELECT id,email,full_name,password_hash,api_token,phone FROM customers ORDER BY id LIMIT 1"
    )
    assert result.ok
    assert result.data.rows == [[1, None, None, None, None, None]]


def test_no_public_columns_still_permits_safe_row_counts(settings):
    service = Service(replace(settings, policy=Policy(tables={"customers": TablePolicy()})))
    result = service.execute_safe_query("SELECT id,email FROM customers LIMIT 1")
    assert result.ok and result.data.rows == [[None, None]]
    assert service.get_table_statistics("customers").data.row_count == 12


def test_unknown_public_column_fails_closed(settings):
    policy = Policy(tables={"customers": TablePolicy(public_columns=["typo"])})
    result = Service(replace(settings, policy=policy)).inspect_schema()
    assert not result.ok and result.error.code == "POLICY_MISMATCH"


@pytest.mark.parametrize(
    "ddl, name",
    [
        ("CREATE VIEW public_view AS SELECT email FROM customers", "public_view"),
        ("CREATE VIRTUAL TABLE search USING fts5(content)", "search"),
        (
            "CREATE TABLE generated (id INTEGER, value TEXT GENERATED ALWAYS AS (id||'secret'))",
            "generated",
        ),
        ('CREATE TABLE unsafe ("bad-column" TEXT)', "unsafe"),
    ],
)
def test_unsupported_objects_fail_closed(settings, db_path, ddl, name):
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(ddl)
    policy = Policy(tables={name: TablePolicy()})
    result = Service(replace(settings, policy=policy)).inspect_schema()
    assert not result.ok and result.error.code == "POLICY_MISMATCH"


def test_default_values_and_triggers_not_exposed(settings, db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("ALTER TABLE customers ADD COLUMN opaque TEXT DEFAULT 'LITERAL_SECRET'")
        conn.execute(
            "CREATE TRIGGER secret_trigger AFTER INSERT ON customers "
            "BEGIN SELECT 'TRIGGER_SECRET'; END"
        )
    result = Service(settings).inspect_schema()
    assert result.ok
    assert "LITERAL_SECRET" not in result.model_dump_json()
    assert "TRIGGER_SECRET" not in result.model_dump_json()


def test_new_nonallowlisted_table_does_not_change_disclosed_fingerprint(service, db_path):
    before = service.inspect_schema().data.fingerprint
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE other_secret(secret TEXT)")
    assert service.inspect_schema().data.fingerprint == before


def test_blob_and_nonfinite_values_are_not_serialized(settings):
    assert safe_cell(b"RAW_SECRET", settings) is None
    assert safe_cell(float("inf"), settings) is None
    assert safe_cell(float("nan"), settings) is None
    assert safe_cell(123, settings) == 123
    with pytest.raises(SafeError):
        safe_cell(object(), settings)


@pytest.mark.parametrize(
    "decl, expected",
    [
        ("INTEGER", "INTEGER"),
        ("VARCHAR(128)", "TEXT"),
        ("BLOB", "BLOB"),
        ("", "BLOB"),
        ("DOUBLE", "REAL"),
        ("DECIMAL", "NUMERIC"),
    ],
)
def test_affinity_is_normalized(decl, expected):
    assert affinity(decl) == expected


@pytest.mark.parametrize(
    "data",
    [
        {"tables": {}},
        {"tables": {"sqlite_schema": {}}},
        {"tables": {"bad;table": {}}},
        {"tables": {"customers": {"public_columns": ["email", "email"]}}},
        {"tables": {"customers": {"public_columns": ["invalid.name"]}}},
        {"tables": {"customers": {"unknown_option": True}}},
        {"tables": {"customers": {}}, "allow_writes": True},
        {"tables": {"customers": {}, "CUSTOMERS": {}}},
    ],
)
def test_invalid_policies_rejected(data):
    with pytest.raises(ValidationError):
        Policy.model_validate(data)


@pytest.mark.parametrize(
    "updates",
    [
        {"max_rows": 0},
        {"max_rows": 1001},
        {"snapshot_max_rows": 0},
        {"snapshot_max_bytes": 0},
        {"timeout_seconds": 31},
        {"max_vm_steps": 0},
        {"max_cell_bytes": 0},
        {"max_response_bytes": 0},
        {"expected_fingerprint": "invalid"},
    ],
)
def test_invalid_settings_rejected(settings, updates):
    with pytest.raises(ValueError):
        replace(settings, **updates)


def test_env_loading(monkeypatch, db_path, policy_path):
    monkeypatch.setenv("MCP_DB_PATH", str(db_path))
    monkeypatch.setenv("MCP_POLICY_PATH", str(policy_path))
    monkeypatch.setenv("MCP_MAX_ROWS", "7")
    settings = Settings.from_env()
    assert settings.db_path == db_path
    assert settings.max_rows == 7
    monkeypatch.setenv("MCP_POLICY_PATH", "relative.json")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_missing_database_not_created(tmp_path, policy):
    path = tmp_path / "missing.db"
    with pytest.raises(ValueError):
        Settings(db_path=path, policy=policy)
    assert not path.exists()
    with pytest.raises(ValueError):
        Settings(db_path=Path("relative.db"), policy=policy)


def test_deleted_database_fails_without_leaking_path(settings, db_path):
    db_path.unlink()
    result = Service(settings).inspect_schema()
    assert not result.ok and result.error.code == "DATABASE_UNAVAILABLE"
    assert str(db_path) not in result.model_dump_json()
    assert not db_path.exists()


def test_demo_determinism_integrity_and_no_overwrite(tmp_path):
    one, two = tmp_path / "one.db", tmp_path / "two.db"
    create_demo(one, 5)
    create_demo(two, 5)
    assert one.read_bytes() == two.read_bytes()
    with pytest.raises(FileExistsError):
        create_demo(one, 5)
    with pytest.raises(ValueError):
        create_demo(tmp_path / "bad.db", 0)
    with closing(sqlite3.connect(one)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT count(*) FROM transactions").fetchone() == (15,)


def test_budget_deadline(settings, monkeypatch):
    budget = Budget(settings)
    monkeypatch.setattr("private_db_mcp.security.time.monotonic", lambda: budget.deadline + 1)
    with pytest.raises(SafeError, match="resource budget"):
        budget.check()
    assert budget.progress() == 1


def test_unexpected_exception_is_sanitized(settings, monkeypatch):
    def explode(*_args):
        raise RuntimeError("password=DO_NOT_LEAK")

    monkeypatch.setattr("private_db_mcp.service.discover", explode)
    result = Service(settings).inspect_schema()
    assert not result.ok and result.error.code == "INTERNAL_ERROR"
    assert "DO_NOT_LEAK" not in json.dumps(result.model_dump())


def test_sensitive_index_order_does_not_reach_snapshot(settings, db_path):
    policy = Policy(tables={"customers": TablePolicy(public_columns=["id"])})
    service = Service(replace(settings, policy=policy))
    before = service.execute_safe_query("SELECT id FROM customers").data.rows
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("UPDATE customers SET email=printf('%03d@example.invalid', 99-id)")
    after = service.execute_safe_query("SELECT id FROM customers").data.rows
    assert before == after


def test_binary_and_large_secret_never_reach_query(settings, db_path):
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("UPDATE customers SET api_token=?", ("SECRET" * 200000,))
    result = Service(settings).execute_safe_query("SELECT api_token FROM customers LIMIT 1")
    assert result.ok and result.data.rows == [[None]]


def test_schema_response_budget(settings):
    result = Service(replace(settings, max_response_bytes=10)).inspect_schema()
    assert not result.ok and result.error.code == "RESOURCE_LIMIT"


def test_foreign_key_unsupported_column_fails_closed(settings, db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute('CREATE TABLE other(id INTEGER REFERENCES customers("bad-column"))')
    policy = Policy(tables={"customers": TablePolicy(), "other": TablePolicy()})
    result = Service(replace(settings, policy=policy)).inspect_schema()
    assert not result.ok and result.error.code == "POLICY_MISMATCH"
