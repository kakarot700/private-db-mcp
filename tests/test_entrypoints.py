import sys

import pytest

from private_db_mcp import demo, server


def test_demo_entrypoint(monkeypatch, tmp_path, capsys):
    path = tmp_path / "cli.db"
    monkeypatch.setattr(sys, "argv", ["private-db-demo", str(path), "--customers", "2"])
    demo.main()
    assert path.is_file()
    assert "Synthetic database created" in capsys.readouterr().out


def test_server_construction_and_entrypoint(monkeypatch, settings):
    instance = server.create_server(settings)
    assert instance.name == "private-db-mcp"
    transports = []
    monkeypatch.setattr(server.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(server.FastMCP, "run", lambda self, transport: transports.append(transport))
    server.main()
    assert transports == ["stdio"]


def test_server_invalid_configuration_entrypoint(monkeypatch, capsys):
    monkeypatch.delenv("MCP_DB_PATH", raising=False)
    monkeypatch.delenv("MCP_POLICY_PATH", raising=False)
    with pytest.raises(SystemExit) as result:
        server.main()
    assert result.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Configuration invalid" in captured.err
