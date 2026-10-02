import json
from pathlib import Path

import pytest

from private_db_mcp.config import Policy, Settings
from private_db_mcp.demo import create_demo
from private_db_mcp.service import Service

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "private ?# database.db"
    create_demo(path, customers=12)
    return path


@pytest.fixture
def policy():
    return Policy.model_validate_json((ROOT / "examples/policy.json").read_text())


@pytest.fixture
def settings(db_path, policy):
    return Settings(db_path=db_path, policy=policy, max_rows=20)


@pytest.fixture
def service(settings):
    return Service(settings)


@pytest.fixture
def policy_path(tmp_path, policy):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy.model_dump()))
    return path
