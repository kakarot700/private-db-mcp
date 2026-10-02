import asyncio
import json
import os
import subprocess
import sys

import jsonschema
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from private_db_mcp.models import QueryData, Result, SchemaData, StatisticsData, VerificationData


@pytest.fixture
def server_parameters(db_path, policy_path):
    return StdioServerParameters(
        command=sys.executable,
        args=["-W", "error", "-m", "private_db_mcp.server"],
        env={
            "MCP_DB_PATH": str(db_path),
            "MCP_POLICY_PATH": str(policy_path),
            "MCP_MAX_ROWS": "20",
            "PYTHONWARNINGS": "error",
        },
    )


async def test_real_stdio_initialization_tools_and_response_schemas(server_parameters, tmp_path):
    with (tmp_path / "server.stderr").open("w+") as errlog:
        async with asyncio.timeout(20):
            async with stdio_client(server_parameters, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    assert initialized.serverInfo.name == "private-db-mcp"
                    tools = (await client.list_tools()).tools
                    mapping = {tool.name: tool for tool in tools}
                    assert set(mapping) == {
                        "inspect_schema",
                        "execute_safe_query",
                        "get_table_statistics",
                        "verify_schema",
                    }
                    for tool in tools:
                        assert tool.annotations.readOnlyHint
                        assert not tool.annotations.destructiveHint
                        assert not tool.annotations.openWorldHint
                        assert tool.outputSchema
                        jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
                        jsonschema.Draft202012Validator.check_schema(tool.outputSchema)

                    schema_result = await client.call_tool("inspect_schema", {})
                    assert not schema_result.isError
                    schema = Result[SchemaData].model_validate(schema_result.structuredContent)
                    assert schema.ok and len(schema.data.tables) == 3
                    calls = [
                        (
                            "execute_safe_query",
                            {
                                "query": "SELECT id,email,api_token FROM customers ORDER BY id",
                                "row_limit": 2,
                            },
                            Result[QueryData],
                        ),
                        ("get_table_statistics", {"table": "transactions"}, Result[StatisticsData]),
                        (
                            "verify_schema",
                            {"expected_fingerprint": schema.data.fingerprint},
                            Result[VerificationData],
                        ),
                    ]
                    for name, args, model in calls:
                        response = await client.call_tool(name, args)
                        assert not response.isError
                        parsed = model.model_validate(response.structuredContent)
                        assert parsed.ok
                        jsonschema.validate(response.structuredContent, mapping[name].outputSchema)
                        if name == "execute_safe_query":
                            assert parsed.data.rows == [[1, None, None], [2, None, None]]
                        if name == "verify_schema":
                            assert parsed.data.matches
                    jsonschema.validate(
                        schema_result.structuredContent, mapping["inspect_schema"].outputSchema
                    )
                    await client.send_ping()
        errlog.seek(0)
        log = errlog.read()
        assert "Warning" not in log and "Traceback" not in log
        assert "DEMO_TOKEN" not in log and str(server_parameters.env["MCP_DB_PATH"]) not in log


async def test_real_stdio_security_errors_and_concurrent_calls(server_parameters):
    async with asyncio.timeout(20):
        async with stdio_client(server_parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                responses = await asyncio.gather(
                    *[
                        client.call_tool("execute_safe_query", {"query": query})
                        for query in [
                            "DELETE FROM customers",
                            "SELECT readfile('/etc/passwd')",
                            "SELECT * FROM sqlite_master",
                            "SELECT count(*) FROM customers WHERE email LIKE 'person%'",
                        ]
                    ]
                )
                for response in responses[:3]:
                    parsed = Result[QueryData].model_validate(response.structuredContent)
                    assert not parsed.ok and parsed.error
                assert responses[3].structuredContent["data"]["rows"] == [[0]]
                response = await client.call_tool("nonexistent", {})
                assert response.isError
                response = await client.call_tool("execute_safe_query", {"row_limit": 1})
                assert response.isError
                response = await client.call_tool(
                    "execute_safe_query", {"query": "SELECT 1", "row_limit": True}
                )
                assert response.isError
                # Failed calls must not kill the server.
                assert not (await client.call_tool("inspect_schema", {})).isError


async def test_wire_is_newline_delimited_jsonrpc(server_parameters):
    env = {**os.environ, **server_parameters.env}
    process = await asyncio.create_subprocess_exec(
        server_parameters.command,
        *server_parameters.args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        async with asyncio.timeout(15):
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "raw-wire-test", "version": "1"},
                },
            }
            process.stdin.write((json.dumps(request) + "\n").encode())
            await process.stdin.drain()
            response = json.loads(await process.stdout.readline())
            assert response["jsonrpc"] == "2.0" and response["id"] == 1
            assert response["result"]["serverInfo"]["name"] == "private-db-mcp"
            process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.write(b'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n')
            await process.stdin.drain()
            response = json.loads(await process.stdout.readline())
            assert response["id"] == 2 and len(response["result"]["tools"]) == 4
            process.stdin.close()
            await process.stdin.wait_closed()
            await process.wait()
            assert process.returncode == 0
            assert await process.stdout.read() == b""
            assert b"Warning" not in await process.stderr.read()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def test_startup_error_does_not_print_credentials_or_paths(tmp_path):
    env = {
        **os.environ,
        "MCP_DB_PATH": "/secret/credential.db",
        "MCP_POLICY_PATH": "/secret/password=DO_NOT_LEAK.json",
    }
    result = subprocess.run(
        [sys.executable, "-W", "error", "-m", "private_db_mcp.server"],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert not result.stdout
    assert "Configuration invalid" in result.stderr
    assert "/secret" not in result.stderr and "DO_NOT_LEAK" not in result.stderr
