# Security and disclosure boundaries

This server is designed to reduce accidental disclosure to a local MCP client. Its security
controls are tested, but passing tests are not a security audit, certification, or a guarantee
against unknown vulnerabilities.

## Assets, adversaries and trust

- **Protected assets:** Unapproved table contents, sensitive cells, schema literal defaults,
  trigger bodies, credentials, filesystem paths, and source database integrity.
- **Untrusted inputs:** Model-authored SQL, parameter values, table arguments, and all database
  content returned to the host. Database text must never be treated as instructions.
- **Trusted inputs:** The server binary, dependencies, owner-controlled policy/configuration,
  database file and its schema, Python interpreter, operating system, and MCP client process.
- **Trusted host boundary:** A local coding agent with shell/filesystem tools might read or
  change the database independently of MCP. These controls do not restrict its other tools.
  Keep the database/policy outside any agent-writable workspace and use a separate OS identity
  or sandbox when this boundary matters.

## Disclosure policy

All columns default to masked, and unlisted tables cannot be queried. A public-column
allowlist is affirmative permission to send those values to the MCP host; sensitive-name
patterns add a non-overridable second gate.

The name detector is not a semantic PII classifier. If an owner approves a column called
`status` containing passwords or sensitive free text, those strings will be disclosed.
Review public fields, use curated reporting tables, and do not approve free-text logs,
messages, descriptions, JSON payloads, or credential-bearing metadata.

Protected values are replaced with NULL in the source projection, so they never enter the
caller-SQL database. BLOB values and non-finite numbers are also converted to NULL.
This is irreversible redaction, not reversible pseudonymization.

Row counts, the existence of approved rows, public identifiers, safe schema names, relationships,
and cross-table joins on approved public keys are intentionally disclosed. The design does not
provide differential privacy or hide changes in these authorized facts.
Very large/corrupt source records and source I/O can affect timing or operation success;
constant-time behavior and protection against physical side channels are not claimed.

## Query isolation

Caller SQL runs only against a private, bounded in-memory snapshot. The snapshot contains no
source triggers, views, defaults, generated expressions, or private tables.
Rows are sorted by sanitized values before insertion to avoid carrying private source-index
ordering into snapshot row order.

SQLite `mode=ro`, source `query_only`, an AST sanitizer, a deny-by-default SQLite authorizer,
disabled loadable extensions, restricted built-in functions, and query-only snapshot mode
provide multiple independent layers. All source reads use trusted SQL and positional
bindings; caller SQL is never executed on the source connection.

The server does create and populate internal in-memory tables as part of sanitization.
The prohibition on writes applies to caller operations and the source database, not to this
isolated implementation detail.

## Credentials, transport and logging

SQLite in this release requires no database password. No credential fields, DSNs, network
endpoints, file selection tools, or remote connection tools are exposed.
The tool service emits static failure messages without source SQL, bindings, paths, or
tracebacks; startup configuration errors are likewise generic.

The SDK owns newline-delimited JSON-RPC on stdout. Operational diagnostics go to stderr.
SQLGlot statement logging is disabled in the CLI to prevent parser diagnostics echoing SQL.
Caller-provided arguments may be echoed by SDK argument-validation errors; do not put
credentials in tool arguments, queries, aliases, client config snippets, or host prompts.

The application makes no runtime network calls. However, MCP responses are handed to your
host, which may forward approved values and schema to a cloud model. Local stdio alone does
not make the host offline. Review the host's privacy, retention, and model settings.

## Availability and production deployment

Deploy under a least-privileged identity with read-only access to approved source files.
Mount the database and policy read-only where possible, keep policy/config inaccessible to
the model's shell, deny outbound network access, and enforce OS memory/CPU/process limits.
Dependency installation is a separate, networked build step.

Application budgets mitigate ordinary abusive queries but do not fully isolate native SQLite,
parser allocations, corrupt files, unbounded MCP input queues, or a hostile same-user process.
The two-worker cap is not an authentication, rate-limiting, or multi-tenant boundary.
Do not expose this stdio process through a public HTTP bridge.

Use an owner-produced, consistent backup when live WAL databases have incompatible filesystem
permissions or checkpoint requirements. Do not copy only the main file of a live WAL database.
No `immutable=1` assumption is made about a database that may change.

Views, virtual/shadow tables, generated columns, non-simple identifiers, attached databases,
and encrypted SQLite variants are unsupported and fail closed. Connections are closed on
success or failure; source access never migrates or repairs a file.

## Residual risk and maintenance

Dependency and SQLite vulnerabilities still matter. Retest the pinned stack when upgrading,
review policy changes as disclosure changes, and use owner-pinned schema fingerprints where
unexpected schema drift must block access.

The fingerprint covers the disclosed schema and masking decisions, not all source DDL,
data values, indexes, triggers, hidden tables, or a full integrity attestation. A successful
verification is not evidence that the database is untampered.

If you identify a vulnerability, stop the server, revoke access to affected data, retain
only sanitized diagnostics, and patch/retest before restarting. There is no invented external
security-reporting endpoint in this package.
