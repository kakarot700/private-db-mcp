"""Exercise installed-wheel entry points and real stdio tools in a clean runtime venv."""

import asyncio
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import private_db_mcp

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    module_path = Path(private_db_mcp.__file__).resolve()
    if "site-packages" not in module_path.parts:
        raise SystemExit("This check must run against the installed wheel, not editable source.")
    with tempfile.TemporaryDirectory(prefix="private-db-wheel-") as temporary:
        db = Path(temporary) / "demo.db"
        demo = subprocess.run(
            [sys.executable, "-W", "error", "-m", "private_db_mcp.demo", str(db)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert demo.stdout.strip() == "Synthetic database created; no real customer data was used."
        assert not demo.stderr
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        params = StdioServerParameters(
            command=sys.executable,
            args=["-W", "error", "-m", "private_db_mcp.server"],
            env={"MCP_DB_PATH": str(db), "MCP_POLICY_PATH": str(ROOT / "examples/policy.json")},
        )
        stderr = Path(temporary) / "stderr.txt"
        with stderr.open("w+") as log:
            async with asyncio.timeout(20):
                async with stdio_client(params, errlog=log) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        tools = sorted(t.name for t in (await client.list_tools()).tools)
                        assert len(tools) == 4
                        schema = (await client.call_tool("inspect_schema", {})).structuredContent
                        assert schema["ok"]
                        query = (
                            await client.call_tool(
                                "execute_safe_query",
                                {
                                    "query": "SELECT id,email,api_token FROM customers ORDER BY id",
                                    "row_limit": 2,
                                },
                            )
                        ).structuredContent
                        assert query["ok"] and query["data"]["rows"] == [
                            [1, None, None],
                            [2, None, None],
                        ]
                        rejected = (
                            await client.call_tool(
                                "execute_safe_query",
                                {
                                    "query": "DROP TABLE customers",
                                },
                            )
                        ).structuredContent
                        assert not rejected["ok"]
                        stats = (
                            await client.call_tool(
                                "get_table_statistics",
                                {
                                    "table": "transactions",
                                },
                            )
                        ).structuredContent
                        assert stats["ok"] and stats["data"]["row_count"] == 360
                        verified = (
                            await client.call_tool(
                                "verify_schema",
                                {
                                    "expected_fingerprint": schema["data"]["fingerprint"],
                                },
                            )
                        ).structuredContent
                        assert verified["ok"] and verified["data"]["matches"]
            log.seek(0)
            assert log.read() == ""
        assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    evidence = {
        "python": platform.python_version(),
        "installed_module": str(module_path),
        "wheel_smoke_passed": True,
        "tools": tools,
        "synthetic_customers": 120,
        "synthetic_transactions": 360,
        "source_unchanged": True,
        "server_stderr_empty": True,
        "masked_query_rows": query["data"]["rows"],
    }
    (ROOT / "reports/wheel-smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
