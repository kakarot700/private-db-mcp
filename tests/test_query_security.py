import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from private_db_mcp.security import Budget, authorizer, harden
from private_db_mcp.service import Service

ATTACKS = [
    "INSERT INTO customers(id) VALUES (999)",
    "UPDATE customers SET status='stolen'",
    "DELETE FROM customers",
    "DROP TABLE customers",
    "ALTER TABLE customers ADD COLUMN hacked TEXT",
    "CREATE TABLE hacked(x)",
    "CREATE TEMP TABLE hacked(x)",
    "REPLACE INTO customers(id) VALUES (1)",
    "VACUUM",
    "VACUUM INTO '/tmp/stolen.db'",
    "ATTACH DATABASE '/tmp/stolen.db' AS stolen",
    "DETACH DATABASE main",
    "PRAGMA writable_schema=ON",
    "PRAGMA query_only=OFF",
    "PRAGMA database_list",
    "PRAGMA table_info(customers)",
    "ANALYZE",
    "REINDEX",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT sneaky",
    "SELECT 1; DROP TABLE customers",
    "SELECT 1; SELECT 2",
    ";SELECT 1",
    "SELECT 1;;",
    "/* safe? */DELETE FROM customers",
    "WITH x AS (SELECT 1) DELETE FROM customers",
    "WITH x AS (SELECT 1) UPDATE customers SET status='x'",
    "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT * FROM x",
    "SELECT load_extension('/tmp/evil.so')",
    "SELECT readfile('/etc/passwd')",
    "SELECT writefile('/tmp/evil','content')",
    "SELECT randomblob(1000000000)",
    "SELECT zeroblob(1000000000)",
    "SELECT sqlite_version()",
    "SELECT sqlite_source_id()",
    "SELECT sql FROM sqlite_master",
    "SELECT name FROM sqlite_schema",
    "SELECT * FROM pragma_database_list",
    "SELECT * FROM pragma_table_info('customers')",
    "SELECT * FROM main.customers",
    "SELECT * FROM temp.customers",
    "SELECT credential FROM internal_secrets",
    "SELECT 1 INTO hacked",
    "SELECT '\x00'",
    "EXPLAIN SELECT * FROM customers",
    "SELECT FROM customers",
]


@pytest.mark.parametrize("query", ATTACKS)
def test_attack_rejected_and_source_unchanged(service, db_path, query):
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    result = service.execute_safe_query(query)
    assert not result.ok, query
    assert result.data is None
    assert result.error is not None
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    assert "DEMO_" not in result.model_dump_json()
    assert str(db_path) not in result.model_dump_json()


@pytest.mark.parametrize(
    "query, expected",
    [
        ("SELECT email AS harmless FROM customers LIMIT 1", [[None]]),
        ("SELECT substr(api_token,1,4) FROM customers LIMIT 1", [[None]]),
        ("SELECT lower(full_name), hex(password_hash) FROM customers LIMIT 1", [[None, ""]]),
        ("SELECT email || api_token FROM customers LIMIT 1", [[None]]),
        ("SELECT count(*) FROM customers WHERE email LIKE 'person%'", [[0]]),
        ("SELECT count(email) FROM customers", [[0]]),
        ("SELECT count(*) FROM customers WHERE api_token='DEMO_TOKEN_1'", [[0]]),
        (
            "SELECT CASE WHEN password_hash='DEMO_HASH_1' THEN 1 ELSE 0 END FROM customers LIMIT 1",
            [[0]],
        ),
        ("SELECT group_concat(auth_token) FROM audit_logs", [[None]]),
        ("WITH x AS (SELECT email AS x FROM customers) SELECT max(x) FROM x", [[None]]),
        ("SELECT email FROM customers UNION SELECT auth_token FROM audit_logs", [[None]]),
        ("SELECT count(*) FROM customers a JOIN customers b ON a.email=b.email", [[0]]),
        ("SELECT count(*) FROM audit_logs WHERE message LIKE '%Private%'", [[0]]),
    ],
)
def test_masking_happens_before_sql(service, query, expected):
    result = service.execute_safe_query(query)
    assert result.ok, result
    assert result.data.rows == expected
    assert "customers.email" in result.data.masked_columns
    assert "DEMO_TOKEN" not in result.model_dump_json()


@pytest.mark.parametrize(
    "limit, expected",
    [
        (-999, 1),
        (0, 1),
        (1, 1),
        (4, 4),
        (20, 20),
        (999999999, 20),
    ],
)
def test_row_limit_clamping(service, limit, expected):
    result = service.execute_safe_query("SELECT id FROM transactions ORDER BY id", row_limit=limit)
    assert result.ok
    assert result.data.row_count == expected
    assert result.data.effective_limit == expected
    assert result.data.truncated


def test_user_sql_limit_cannot_bypass_server_limit(service):
    result = service.execute_safe_query("SELECT id FROM transactions LIMIT -1", row_limit=3)
    assert result.ok and result.data.row_count == 3 and result.data.truncated


@pytest.mark.parametrize(
    "query",
    [
        "SELECT id FROM customers LIMIT 1",
        "SELECT id FROM customers WHERE id < 0",
        "SELECT id FROM customers LIMIT 3",
    ],
)
def test_truncation_only_when_extra_row_exists(service, query):
    result = service.execute_safe_query(query, row_limit=3)
    assert result.ok
    assert not result.data.truncated


def test_parameter_injection_is_data(service):
    injection = "' OR 1=1; DROP TABLE customers; --"
    result = service.execute_safe_query("SELECT id FROM customers WHERE status = ?", [injection])
    assert result.ok and result.data.rows == []
    assert service.execute_safe_query("SELECT count(*) FROM customers").data.rows == [[12]]


def test_bindings_and_read_only_cte_join_aggregate(service):
    result = service.execute_safe_query(
        "WITH totals AS (SELECT customer_id, SUM(amount_cents) AS cents "
        "FROM transactions GROUP BY customer_id) "
        "SELECT c.id, t.cents FROM customers c JOIN totals t ON c.id=t.customer_id "
        "WHERE c.id<=? ORDER BY c.id",
        [2],
    )
    assert result.ok, result
    assert len(result.data.rows) == 2
    assert all(row[1] > 0 for row in result.data.rows)


def test_literals_and_comments_not_naive_keyword_blacklist(service):
    result = service.execute_safe_query(
        "/* DROP TABLE customers; */ SELECT 'delete; update drop --' AS text"
    )
    assert result.ok
    assert result.data.rows == [["delete; update drop --"]]


@pytest.mark.parametrize(
    "query, params, limit",
    [
        ("", None, 1),
        (" " * 5, None, 1),
        ("x" * 17000, None, 1),
        ("SELECT 1", None, True),
        ("SELECT 1", None, "20"),
        ("SELECT ?", [{"bad": "binding"}], 1),
        ("SELECT ?", [True], 1),
        ("SELECT ?", ["x" * 20000], 1),
        ("SELECT ?", list(range(129)), 1),
        ("SELECT ?", "not-a-list", 1),
    ],
)
def test_invalid_inputs_fail_safely(service, query, params, limit):
    result = service.execute_safe_query(query, params, limit)
    assert not result.ok
    assert "bad" not in result.model_dump_json()


def test_missing_bindings_and_sql_errors_are_generic(service):
    for query in [
        "SELECT ?",
        "SELECT unknown FROM customers",
        "SELECT 'super-secret' FROM does_not_exist",
    ]:
        result = service.execute_safe_query(query)
        assert not result.ok
        assert result.error.code == "QUERY_FAILED"
        assert "super-secret" not in result.model_dump_json()
        assert "does_not_exist" not in result.model_dump_json()


def test_vm_guard_independent_of_parser(settings):
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as conn:
        harden(conn, Budget(settings))
        conn.execute("CREATE TABLE visible (id INTEGER)")
        conn.execute("INSERT INTO visible VALUES (1)")
        # Deliberately omit query_only and the AST sanitizer to test the VM guard alone.
        conn.set_authorizer(authorizer({"visible": {"id"}}, functions=True))
        assert conn.execute("SELECT count(*) FROM visible").fetchone() == (1,)
        for query in [
            "UPDATE visible SET id=2",
            "DELETE FROM visible",
            "DROP TABLE visible",
            "CREATE TABLE hidden(x)",
            "PRAGMA query_only=OFF",
            "SELECT name FROM sqlite_master",
            "SELECT randomblob(1)",
            "ATTACH ':memory:' AS other",
        ]:
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(query)


def test_native_source_readonly_even_without_authorizer(settings):
    uri = settings.db_path.as_uri() + "?mode=ro"
    with (
        closing(sqlite3.connect(uri, uri=True)) as conn,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        conn.execute("DELETE FROM customers")


@pytest.mark.parametrize(
    "updates",
    [
        {"snapshot_max_rows": 1},
        {"snapshot_max_bytes": 10},
        {"max_vm_steps": 1},
        {"max_response_bytes": 10},
        {"max_cell_bytes": 1},
    ],
)
def test_resource_budgets(settings, updates):
    result = Service(replace(settings, **updates)).execute_safe_query("SELECT * FROM customers")
    assert not result.ok
    assert result.error.code == "RESOURCE_LIMIT"


def test_expensive_join_is_interrupted(settings):
    result = Service(replace(settings, max_vm_steps=20_000)).execute_safe_query(
        "SELECT count(*) FROM transactions a CROSS JOIN transactions b "
        "CROSS JOIN transactions c CROSS JOIN transactions d"
    )
    assert not result.ok
    assert result.error.code == "RESOURCE_LIMIT"


def test_metadata_cannot_reveal_credentials(service):
    text = json.dumps(service.inspect_schema().model_dump())
    assert "internal_secrets" not in text
    assert "DEMO_" not in text
    assert "password_hash" in text  # The schema, but never the value, is disclosed.
