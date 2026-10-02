import sqlite3
from contextlib import closing

import pytest

from private_db_mcp.security import SafeError, validate_query


@pytest.mark.parametrize(
    "keyword", ["DROP TABLE customers", "DELETE FROM customers", "UPDATE customers SET status='x'"]
)
@pytest.mark.parametrize("prefix", ["-- comment\n", "/* comment */ ", "\n\t"])
@pytest.mark.parametrize("case", [str.lower, str.upper, str.swapcase])
def test_comment_case_variations_remain_blocked(service, keyword, prefix, case):
    result = service.execute_safe_query(prefix + case(keyword))
    assert not result.ok


def test_ast_size_budget():
    sql = "SELECT " + ",".join("1" for _ in range(1100))
    with pytest.raises(SafeError, match="resource budget"):
        validate_query(sql)


def test_deep_parse_input_fails_without_traceback(service):
    result = service.execute_safe_query("SELECT " + "(" * 2000 + "1" + ")" * 2000)
    assert not result.ok
    assert "Traceback" not in result.model_dump_json()


def test_duplicate_projection_labels_are_preserved(service):
    result = service.execute_safe_query("SELECT 1 AS x, 2 AS x")
    assert result.ok
    assert result.data.columns == ["x", "x"]
    assert result.data.rows == [[1, 2]]


def test_actual_nulls_public_statistics(settings, db_path):
    from private_db_mcp.service import Service

    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("ALTER TABLE customers ADD COLUMN optional_value INTEGER")
    policy_data = settings.policy.model_dump()
    policy_data["tables"]["customers"]["public_columns"].append("optional_value")
    from dataclasses import replace

    from private_db_mcp.config import Policy

    svc = Service(replace(settings, policy=Policy.model_validate(policy_data)))
    result = svc.get_table_statistics("customers")
    col = next(c for c in result.data.columns if c.column == "optional_value")
    assert col.null_count == 12 and not col.masked


def test_sqlite_function_allocation_limit(service):
    result = service.execute_safe_query("SELECT printf('%1000000000s','x')")
    # Depending on SQLite, allocation-limit errors become NULL or a generic failure.
    assert not result.ok or result.data.rows == [[None]]


def test_unicode_data_roundtrip(service):
    result = service.execute_safe_query("SELECT ? AS value", ["नमस्ते"])
    assert result.ok and result.data.rows == [["नमस्ते"]]


def test_large_numeric_binding_does_not_crash(service):
    result = service.execute_safe_query("SELECT ?", [2**100])
    assert not result.ok and result.error.code == "QUERY_FAILED"
