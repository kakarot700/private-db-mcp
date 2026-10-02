# Private Database MCP

A local SQLite introspection server with deny-by-default disclosure and four typed MCP tools.
It uses the official Python SDK's FastMCP over stdio, not a handwritten JSON-RPC approximation
([MCP Python SDK](https://py.sdk.modelcontextprotocol.io/v1/)).

The implementation provides `inspect_schema`, `execute_safe_query`,
`get_table_statistics`, and `verify_schema`. It includes a deterministic synthetic database
generator, real stdio integration tests, a dependency lock, client configurations, and repeatable
verification tooling.

## Quick start

Use Python 3.12+ and `uv`. Commands below run from the extracted project directory;
the server and tests do not require API keys, subscriptions, GPUs, or external services.

```sh
uv sync --locked
uv run private-db-demo "$PWD/demo.db"
export MCP_DB_PATH="$PWD/demo.db"
export MCP_POLICY_PATH="$PWD/examples/policy.json"
uv run python scripts/verify.py
uv run private-db-mcp
```

The last command starts the stdio server and waits for an MCP client. It intentionally prints
no banner on stdout; normally let your client launch it rather than launching it manually.
The demo command refuses to overwrite any existing file.

For a production runtime without test dependencies:

```sh
uv sync --locked --no-dev
```

Alternatively, with a Python virtual environment and pip:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps dist/private_db_mcp-1.0.0-py3-none-any.whl
```

The complete ZIP also includes a built wheel. Installing that wheel needs no build backend;
the hash-locked requirements file installs its runtime dependencies first.

## Security model

- **Read-only source:** SQLite opens an existing owner-configured file using `mode=ro`;
  `query_only`, `trusted_schema=OFF`, disabled extensions and an authorizer add independent gates.
- **Deny-by-default disclosure:** Only policy-listed tables exist in the query snapshot.
  Undeclared columns and sensitive-name matches become SQL `NULL` before caller SQL runs.
  Raw log messages, passwords, tokens, and PII columns are masked by the included demo policy.
- **Inference-resistant masking:** Aliases, CTEs, predicates, joins, string functions and
  aggregates operate on sanitized values, not raw data. Snapshot ordering is derived from
  sanitized rows, preventing private source-index order from reaching unordered queries.
- **Restricted SQL:** A SQLGlot AST gate permits one SELECT/set-operation statement.
  A separate SQLite VM authorizer blocks all caller writes, PRAGMA, attachment, schema tables,
  implicit rowids, and non-allowlisted functions.
- **Bounded operations:** Row clamping, input/AST/SQLite limits, VM-step/deadline guards,
  snapshot and response budgets, and two worker slots constrain requests.
- **Credential-safe operation:** No connection strings or credentials are accepted as tool
  arguments. Safe service errors omit SQL, parameters, native exceptions, paths and tracebacks.
  The server has no telemetry or network client calls.

This is a production-oriented implementation for **trusted local SQLite files and trusted
local MCP hosts**, not an audited security product or a general-purpose database gateway.
PostgreSQL, MySQL, remote authentication, arbitrary log files, and multi-tenant HTTP access
are intentionally outside this release. Read `SECURITY.md` before using private data.

## Tools and response contract

| Tool | Inputs | Successful data |
| --- | --- | --- |
| `inspect_schema` | None | Approved tables, columns, keys, masking flags, SHA-256 fingerprint, Mermaid ER text |
| `execute_safe_query` | `query`, optional positional `parameters`, `row_limit` | Column array, positional row arrays, count, effective limit, truncation flag, protected-column list |
| `get_table_statistics` | Approved `table` | Exact row count, public-column NULL counts; masked-column statistics withheld |
| `verify_schema` | `expected_fingerprint` | Match boolean and current fingerprint |

Every tool has a Pydantic-generated output schema and returns the same envelope:

```json
{
  "ok": true,
  "data": {
    "columns": ["id", "email"],
    "rows": [[1, null]],
    "row_count": 1,
    "effective_limit": 1,
    "truncated": true,
    "masked_columns": ["customers.email"]
  },
  "error": null
}
```

The example shortens `masked_columns` for readability. Successful query responses contain the
complete protected-column list for the disclosed schema, not lineage for computed result columns.
Rows are positional arrays so duplicate SQL column labels never silently overwrite values.

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "QUERY_REJECTED",
    "message": "Only a single permitted read-only SELECT query is allowed."
  }
}
```

Application-level failures use `ok=false` inside the structured response. MCP protocol errors,
unknown tools and argument-schema validation errors use the SDK's standard `isError` behavior.
Clients must inspect both `isError` and `structuredContent.ok`.

## Query examples

Pass untrusted values in `parameters`, never by concatenating SQL. Identifiers are not parameter
values; table access is restricted to the owner's local policy.

```json
{
  "query": "SELECT id, status, email FROM customers WHERE id = ?",
  "parameters": [1],
  "row_limit": 10
}
```

```sql
SELECT customer_id, SUM(amount_cents) AS total_cents
FROM transactions
WHERE status = 'settled'
GROUP BY customer_id
ORDER BY total_cents DESC
```

```sql
SELECT event_type, COUNT(*) AS events
FROM audit_logs
GROUP BY event_type
ORDER BY events DESC
```

`inspect_schema` includes Mermaid ER text for a local Mermaid-capable Markdown preview.
It is a diagram specification, not a hosted visualization; do not paste private schema into
an online renderer unless you have approved that disclosure.

## Configuration and operation

See [client setup](docs/CLIENTS.md) for Claude Code, Claude Desktop, Cursor, and Windsurf.
See [architecture and operations](docs/ARCHITECTURE.md) for limits, freshness, schema pinning,
failure semantics, and deployment boundaries.

The policy file is an owner-controlled disclosure decision, not something the model can change.
For a different database, copy `examples/policy.json`, list only intended tables and reviewed
public columns, protect the file, and set absolute `MCP_DB_PATH` / `MCP_POLICY_PATH` paths.
New columns are masked automatically; new tables remain inaccessible.

## Verification and package layout

```sh
uv sync --locked
uv run python scripts/verify.py
uv build
```

`reports/verification.json` and `reports/pytest.txt` contain the observed result and environment.
Warnings are treated as errors, not suppressed; stdio subprocesses also run with `-W error`.
The tests exercise actual MCP initialization, tool discovery, schema-validated calls,
concurrency, error recovery, JSON-RPC wire framing, and EOF shutdown.

```text
private-db-mcp/
├── README.md
├── SECURITY.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── requirements.lock
├── src/private_db_mcp/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── demo.py
│   ├── models.py
│   ├── security.py
│   ├── server.py
│   └── service.py
├── examples/
│   ├── policy.json
│   ├── claude_desktop_config.json
│   ├── claude_code.mcp.json
│   ├── cursor.mcp.json
│   └── windsurf.mcp_config.json
├── docs/
│   ├── ARCHITECTURE.md
│   └── CLIENTS.md
├── scripts/
│   ├── setup_demo.py
│   ├── smoke_wheel.py
│   ├── verify.py
│   └── package.py
├── tests/
│   ├── conftest.py
│   ├── test_entrypoints.py
│   ├── test_mcp_stdio.py
│   ├── test_query_security.py
│   ├── test_schema_policy.py
│   └── test_sql_edge_cases.py
├── .github/workflows/ci.yml
├── dist/                         # built wheel and source distribution
└── reports/                      # observed test proof; not included in wheel
```
