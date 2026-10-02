# Private Database MCP: delivery and verification

The implementation, tests, documentation, client configuration templates, dependency locks,
installable wheel, and source distribution are included in the complete delivery ZIP.
The observed test runs met the requested stop condition: zero failures and zero warnings.

## Observed results

| Check | Result |
| --- | --- |
| Python 3.12.13 full Pytest suite | 185 passed, 0 failed, 0 errors, 0 skipped, 0 warnings |
| Python 3.14.3 full Pytest suite | 185 passed, 0 failed, 0 errors, 0 skipped, 0 warnings |
| SQLite tested in both environments | 3.50.4 |
| Ruff lint | All checks passed |
| Ruff formatting | All checked files formatted |
| Coverage, Python 3.12 | 98% combined statement/branch coverage, rounded by coverage.py |
| Build | Wheel and source distribution built successfully |
| Clean wheel installation | Hash-locked runtime dependencies installed in a fresh environment |
| Installed-wheel stdio smoke | All four tools passed; sensitive fields NULL; DROP rejected |
| Source-integrity smoke | Source SHA-256 unchanged after tool calls |
| Smoke stderr | Empty |

The Python 3.12 verification command is reproducible with `uv run python scripts/verify.py`.
It treats warnings as errors; the server subprocess tests also run under `-W error`.
No warning-ignore rule was used to obtain the successful result.

## Included evidence

- **`reports/verification.json`:** Environment, package versions, actual commands, exit codes,
  JUnit totals and warnings policy for the main verification run.
- **`reports/pytest.txt`:** Full final Pytest output and coverage table.
- **`reports/junit.xml`:** Machine-readable primary test results.
- **`reports/junit-py314.xml`:** Machine-readable second-runtime results.
- **`reports/coverage.json`:** Detailed statement and branch coverage.
- **`reports/lint.txt` and `reports/format.txt`:** Static-quality check output.
- **`reports/wheel-smoke.json`:** Observed clean-install smoke results, including installed
  module location proving the test used the wheel rather than editable source.
- **`private-db-mcp-1.0.0.zip.sha256`:** Separate whole-archive checksum delivered with the ZIP.

Coverage is not claimed to be 100%. The real stdio tests run the server in subprocesses, and
the primary coverage configuration does not count those subprocess lines, although their
protocol responses are asserted by the tests.

## Functional and adversarial checks

- **Protocol:** Real initialization, tools/list, all four tools, Pydantic and JSON Schema
  response validation, ping, concurrent calls, invalid-tool recovery, newline JSON-RPC and EOF.
- **Read-only safety:** INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, REPLACE, VACUUM, ATTACH,
  PRAGMA, transactions, multiple statements, comment/case variants and direct VM-layer attacks.
- **Disclosure safety:** Sensitive aliases, expressions, concatenation, aggregates, predicates,
  joins, CTEs, unions, free-text logs, schema defaults, trigger literals and private index order.
- **Schema/policy:** Relationships, Mermaid output, fingerprint drift, owner pins, unapproved
  tables, automatic masking despite sensitive-column allowlisting, and unsupported schema objects.
- **Availability:** Row clamping, source-row and byte budgets, output limits, AST size,
  expression depth, expensive joins, VM interruption, text limits and large function allocations.
- **Packaging:** Deterministic synthetic data, no accidental demo overwrite, database integrity,
  entry points, startup redaction and a fresh installed-wheel MCP smoke test.

The suite is broad but not exhaustive. It does not establish resistance to unknown native
SQLite/SDK/parser vulnerabilities or replace an independent security review.

## Release scope

This release targets local/private SQLite databases and SQLite-backed structured audit logs.
It does not implement PostgreSQL/MySQL adapters, arbitrary filesystem log ingestion, HTTP
deployment, remote authorization, multi-tenant access, or general shell-tool execution.

Client configuration snippets are provided for Claude Desktop, Claude Code, Cursor and
Windsurf/Cascade. Actual GUI-client sessions were not run in this sandbox; interoperability
was exercised through the official SDK and raw stdio protocol.

Before private deployment, review `SECURITY.md`, use least-privileged filesystem permissions,
approve the table/column policy, keep it outside agent-writable locations, and apply OS resource
limits. Test success is evidence about this build, not a blanket production-security guarantee.

## Start here

Extract the ZIP and open `private-db-mcp/README.md`. The directory layout and quick start are
there; client-specific instructions are in `docs/CLIENTS.md`.

```sh
cd private-db-mcp
uv sync --locked
uv run private-db-demo "$PWD/demo.db"
uv run python scripts/verify.py
```

After verification, edit the appropriate client template with absolute paths to the virtual
environment Python, database and owner policy. The client then launches the stdio process.
