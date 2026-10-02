"""Owner-only policy/configuration. No credentials or paths enter tool results."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
# A second safety net, not a PII classifier. Undeclared columns are ALWAYS masked.
SENSITIVE = re.compile(
    r"password|passwd|pwd|secret|token|credential|auth|api_?key|private_?key|"
    r"email|phone|mobile|address|ssn|social|birth|dob|passport|"
    r"first_?name|last_?name|full_?name|username|ip_?address|"
    r"(^|_)(name|ip|pan|iban|cvv|card|cookie|session|message|payload|"
    r"details|body|text|notes|user_agent)($|_)",
    re.IGNORECASE,
)


def identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value) or value.lower().startswith("sqlite_"):
        raise ValueError("Unsupported identifier.")
    return value


class TablePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    public_columns: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("public_columns")
    @classmethod
    def validate_columns(cls, values: list[str]) -> list[str]:
        for value in values:
            identifier(value)
        if len(set(values)) != len(values):
            raise ValueError("Duplicate column.")
        return values


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tables: dict[str, TablePolicy] = Field(min_length=1, max_length=32)

    @field_validator("tables")
    @classmethod
    def validate_tables(cls, values: dict[str, TablePolicy]) -> dict[str, TablePolicy]:
        for value in values:
            identifier(value)
        if len({v.lower() for v in values}) != len(values):
            raise ValueError("Ambiguous tables.")
        return values


@dataclass(frozen=True)
class Settings:
    db_path: Path
    policy: Policy
    max_rows: int = 200
    snapshot_max_rows: int = 50_000
    snapshot_max_bytes: int = 16_000_000
    max_cell_bytes: int = 16_384
    max_response_bytes: int = 1_000_000
    timeout_seconds: float = 2.0
    max_vm_steps: int = 2_000_000
    expected_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not self.db_path.is_absolute() or not self.db_path.is_file():
            raise ValueError("Database must be an existing absolute file path.")
        if not 1 <= self.max_rows <= 1000:
            raise ValueError("Invalid row bound.")
        if not 1 <= self.snapshot_max_rows <= 1_000_000:
            raise ValueError("Invalid snapshot row bound.")
        if not 1 <= self.snapshot_max_bytes <= 64_000_000:
            raise ValueError("Invalid snapshot byte bound.")
        if not 1 <= self.timeout_seconds <= 30:
            raise ValueError("Invalid deadline.")
        if not 1 <= self.max_vm_steps <= 10_000_000:
            raise ValueError("Invalid instruction bound.")
        if not 1 <= self.max_cell_bytes <= 65536:
            raise ValueError("Invalid cell bound.")
        if not 1 <= self.max_response_bytes <= 4_000_000:
            raise ValueError("Invalid response bound.")
        if self.expected_fingerprint and not FINGERPRINT.fullmatch(self.expected_fingerprint):
            raise ValueError("Invalid fingerprint.")

    @classmethod
    def from_env(cls) -> "Settings":
        policy_path = Path(os.environ["MCP_POLICY_PATH"])
        if not policy_path.is_absolute() or policy_path.stat().st_size > 65536:
            raise ValueError("Invalid policy path or size.")
        policy = Policy.model_validate(json.loads(policy_path.read_text(encoding="utf-8")))
        return cls(
            db_path=Path(os.environ["MCP_DB_PATH"]),
            policy=policy,
            max_rows=int(os.environ.get("MCP_MAX_ROWS", "200")),
            expected_fingerprint=os.environ.get("MCP_EXPECTED_FINGERPRINT") or None,
        )
