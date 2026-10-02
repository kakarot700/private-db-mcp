# Client configuration

Install the package and generate the synthetic database first. Replace every `/ABSOLUTE/...`
placeholder with a real absolute local path; no API key or database credential belongs in these
settings.

Use the environment's Python directly, rather than relying on a GUI application's shell PATH:

```json
{
  "mcpServers": {
    "private-db": {
      "command": "/ABSOLUTE/private-db-mcp/.venv/bin/python",
      "args": ["-m", "private_db_mcp.server"],
      "env": {
        "MCP_DB_PATH": "/ABSOLUTE/private-db-mcp/demo.db",
        "MCP_POLICY_PATH": "/ABSOLUTE/private-db-mcp/examples/policy.json",
        "MCP_MAX_ROWS": "200"
      }
    }
  }
}
```

On Windows, use a JSON-escaped path such as
`C:\\Users\\YOU\\private-db-mcp\\.venv\\Scripts\\python.exe`, and change the database and
policy paths similarly. Merge the server entry into existing settings; do not overwrite
unrelated servers.

## Claude Desktop

Use **Settings → Developer → Edit Config** to edit `claude_desktop_config.json`; the documented
locations are `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS and
`%APPDATA%\Claude\claude_desktop_config.json` on Windows
([MCP local-server setup](https://modelcontextprotocol.io/quickstart/user)).

Copy `examples/claude_desktop_config.json`, substitute absolute paths, save, and restart the
application. Approve the server and test `inspect_schema` before allowing queries on private data.

## Claude Code

The CLI supports local stdio servers with `claude mcp add --transport stdio` and environment
settings passed with `--env`
([Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)).

```sh
claude mcp add --transport stdio \
  --env MCP_DB_PATH=/ABSOLUTE/private-db-mcp/demo.db \
  --env MCP_POLICY_PATH=/ABSOLUTE/private-db-mcp/examples/policy.json \
  --env MCP_MAX_ROWS=200 \
  private-db -- /ABSOLUTE/private-db-mcp/.venv/bin/python -m private_db_mcp.server
```

Alternatively, merge `examples/claude_code.mcp.json` into project `.mcp.json`; use the CLI for
user-level installation and confirm connection with `claude mcp get private-db` or `/mcp`
([Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)).

Do not store sensitive paths or owner policy in a shared repository without reviewing who can
read them. Project-local settings are convenient, but a model with write access to that project
must not control the authoritative security policy.

## Cursor

Cursor documents local command-based stdio settings in `.cursor/mcp.json` for a project or
`~/.cursor/mcp.json` globally
([Cursor MCP configuration](https://cursor.com/help/customization/mcp)).

Copy the entry in `examples/cursor.mcp.json`, including `"type": "stdio"`, into the chosen file,
then enable the server in Cursor's MCP settings; the current reference describes `type`,
`command`, `args` and `env` fields
([Cursor MCP reference](https://cursor.com/docs/mcp)).

Ask the agent to run `inspect_schema`, then a parameterized `execute_safe_query`. Review which
tool calls are approved automatically and the data disclosure implications before changing
those settings.

## Windsurf / Cascade

Use the MCP settings UI's **View Raw Config** or **Open MCP config file** action, and merge
`examples/windsurf.mcp_config.json`; the documented local form uses `mcpServers`, `command`,
`args` and `env`
([Windsurf/Cascade MCP documentation](https://docs.windsurf.com/windsurf/cascade/mcp)).

Configuration paths vary across product generations: the older Windsurf documentation names
`~/.codeium/windsurf/mcp_config.json`, while the current English page describes the legacy
Cascade configuration under `~/.config/devin/mcp_config.json` and separate setup for Devin Local
([Windsurf path documentation](https://docs.windsurf.com/de/windsurf/cascade/mcp),
[current Cascade documentation](https://docs.windsurf.com/windsurf/cascade/mcp)).

Use the file opened by your installed client's UI rather than guessing which path applies.
The stdio server itself is client-independent; no HTTP endpoint or API key is required.

## First connection and troubleshooting

1. Run `uv run python scripts/verify.py` from this project's directory.
2. Generate `demo.db`, edit the example configuration, and restart the client.
3. Confirm exactly four tools appear and call `inspect_schema`.
4. Query `SELECT id,email FROM customers ORDER BY id` with `row_limit: 2`.
5. Verify that `email` is `null`, then confirm a `DELETE FROM customers` call is rejected.
6. Only then switch to an owner-reviewed private database and policy.

An immediately exiting server usually means a missing/relative path, invalid policy JSON, or
an invalid row limit. The startup error is deliberately generic; inspect paths locally.
If no tools appear, verify that the absolute Python executable imports `private_db_mcp` and
that nothing else writes banners to the server's stdout.

The examples are configuration templates, not evidence of GUI-client installation tests.
The bundled integration tests verify the actual stdio handshake, discovery and calls against
the official Python SDK and newline-delimited JSON-RPC.
