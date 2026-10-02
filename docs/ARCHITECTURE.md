# Architecture and operations

The service separates protocol handling, policy validation, metadata discovery, redacted
snapshot construction, query execution, and typed response generation. SQLite-only scope is
intentional: adding another database requires a native read-only role and a new verified
adapter, not a connection-string switch.

## Request flow

```text
Local MCP host
    │ stdin / stdout JSON-RPC
    ▼
Official SDK FastMCP: handshake, tools/list, tools/call, input/output schemas
    │ bounded worker execution (two slots)
    ▼
Service: input checks, operation budgets, generic safe errors
    │
    ├─ inspect / verify ─► read-only source metadata ─► fingerprint + ER text
    │
    └─ query / statistics
          ├─ open source mode=ro + query_only + trusted_schema=OFF
          ├─ read consistent metadata and approved projections in a transaction
          ├─ replace protected cells with NULL before source values are fetched
          ├─ check row/byte/cell budgets; sanitize BLOB/non-finite cells
          ├─ canonicalize sanitized row order; build in-memory tables
          ├─ close source; freeze snapshot with query_only + VM authorizer
          └─ execute permitted SELECT; cap rows/bytes; return typed JSON
```

FastMCP provides the protocol implementation and stdio client/server primitives
([MCP Python SDK server documentation](https://py.sdk.modelcontextprotocol.io/v1/server/)).
This project uses the official SDK's `mcp.server.fastmcp.FastMCP`, not the separately
distributed `fastmcp` package.

## Consistency, masking and statistics

Each call creates new connections. Source schema and data for a query are read within one
SQLite read transaction, and the source closes before caller SQL runs on the snapshot.
Results therefore reflect the database at that operation's snapshot, not a process-lifetime cache.
Separate tool calls may observe different committed states.

NULL redaction deliberately changes query semantics. `COUNT(email)` returns zero for a masked
email column, joins on masked keys cannot match, and `WHERE email IS NULL` sees every protected
cell as NULL regardless of source value. This prevents raw-value predicates from becoming an
oracle, but it is not transparent execution against the original database.

`get_table_statistics` returns exact approved-table row counts and public-column NULL counts.
Protected-column NULL counts are withheld rather than revealing actual source nullness.
There are no min/max samples, cardinality reports on secrets, or raw log previews.

Queries and statistics snapshot all approved tables and fail rather than silently sample when
budgets are exceeded. This trades scalability for isolation and exactness. Use curated, small
read-only reporting databases for larger production sources.

## Configurable and fixed limits

| Control | Default / fixed bound | Configuration |
| --- | --- | --- |
| Returned rows | Request default 100; server cap 200; hard maximum 1000 | `MCP_MAX_ROWS` |
| Snapshot rows | 50,000 total across approved tables | `Settings`, owner-side code |
| Snapshot serialized cell budget | 16,000,000 bytes | `Settings`, max 64,000,000 |
| Individual text cell | 16,384 UTF-8 bytes | `Settings`, max 65,536 |
| Result data JSON | 1,000,000 bytes | `Settings`, max 4,000,000 |
| Per-operation deadline | 2 seconds | `Settings`, 1..30 seconds |
| Approximate VM budget | 2,000,000 steps, progress callbacks in batches of at most 100 | `Settings` |
| Source lock wait | 0.2 seconds | Fixed |
| SQL input | 16,384 UTF-8 bytes | Fixed |
| AST nodes | 1,024 | Fixed |
| Positional parameters | 128 | Fixed |
| Approved tables / columns per table | 32 / 128 | Fixed |
| Concurrent workers | 2 | Fixed |
| SQLite expression depth / compounds | 64 / 16 | Fixed |
| SQLite intermediate value length | 1,000,000 bytes | Fixed |
| Snapshot SQLite pages | 20,000 pages | Fixed |

The serialized snapshot budget is not process RSS, and output byte limits do not include
the MCP envelope or the SDK's parallel text/structured representation. They are conservative
application checks, not substitutes for OS resource limits. SQLite progress callbacks are
approximate, not a hard CPU instruction meter; cancellation of an in-flight worker may wait for
its operation deadline.

Row limits are clamped with `max(1, min(requested, configured_max))`; zero or negative inputs
cannot mean unlimited. Fetching one additional row determines `truncated`; caller SQL such as
`LIMIT -1` cannot bypass the output cap. Large sorts/aggregates are bounded separately by the
VM/deadline controls, since an output limit alone does not limit query computation.

## Owner policy and schema pinning

The only environment settings are:

```text
MCP_DB_PATH                 Required existing absolute local SQLite path
MCP_POLICY_PATH             Required absolute local policy JSON path, at most 64 KiB
MCP_MAX_ROWS                Optional integer, default 200, range 1..1000
MCP_EXPECTED_FINGERPRINT    Optional lowercase 64-character SHA-256
```

Configuration loads once at startup; changing a policy requires restarting the server.
Metadata is rediscovered on each call and new columns remain masked by default.
If an approved table or public column disappears, or an unsupported schema object is approved,
the operation fails closed.

To pin a reviewed schema:

1. Start with an owner-reviewed policy and inspect the schema.
2. Review table names, columns, relationships and masking flags, then copy `data.fingerprint`.
3. Set `MCP_EXPECTED_FINGERPRINT` to that value in the owner-managed client configuration.
4. Restart the server. Every operation now fails with `SCHEMA_MISMATCH` on disclosed-schema drift.

`verify_schema` without an owner pin reports a match or mismatch against its supplied fingerprint.
When an owner pin is set, it cannot be bypassed by passing a different fingerprint to the tool.
The hash includes stable JSON of disclosed column order, affinity, declared nullability, primary
key positions, foreign keys and masking flags; it excludes source default literals and raw DDL.

Mermaid relationship lines indicate discovered foreign-key associations only. Their generic
optional cardinality markers do not assert uniqueness, enforcement, or exact relationship
cardinality; use the structured key metadata for programmatic verification.

## Failure handling

| Application error | Meaning / next action |
| --- | --- |
| `INVALID_INPUT` | Correct argument types, length, fingerprint, or approved table name |
| `QUERY_REJECTED` | Use one supported read-only SELECT; remove forbidden syntax |
| `QUERY_FAILED` | Check approved schema, bindings and function support; native errors remain private |
| `RESOURCE_LIMIT` | Reduce source/reporting database size or query complexity; review owner budgets |
| `DATABASE_UNAVAILABLE` | Owner checks local path and file permissions |
| `SCHEMA_MISMATCH` | Owner reviews drift and explicitly approves a new fingerprint if appropriate |
| `POLICY_MISMATCH` | Owner fixes stale policy or unsupported schema |
| `INTERNAL_ERROR` | Operation failed safely; reproduce using synthetic data before debugging |

No SQL or values are written to an application audit log. Native SQLite errors are intentionally
generic; investigation should occur locally under the owner's authority rather than exposing
database internals to the model.

## Operational checklist

- **Least privilege:** Use dedicated filesystem permissions and a read-only mount. Keep policy
  and database outside agent write scopes; stdio clients otherwise inherit user privileges.
- **Deployment:** Install the locked dependencies, review the source, run verification, and use
  the installed environment's absolute Python executable in client settings.
- **Resources:** Set OS/container limits and deny runtime egress. If a client has broader tools,
  those require independent sandboxing and permissions.
- **Updates:** Review lock changes and schema-policy changes, rerun tests with warnings as errors,
  and roll back by restoring the previous environment and policy.
- **Backup:** The server never performs backup, migration, checkpoint, or repair operations.
  Produce consistent SQLite backups using a separate owner-controlled workflow.

Actual Claude/Cursor/Windsurf GUI sessions and OS-level deployment confinement were not exercised
in this sandbox. Protocol integration is verified against the official SDK and raw JSON-RPC,
with client configuration examples based on the vendors' documented formats.
