"""Versioned, immutable serving fixtures; no model or research behavior is implied."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fall_detection.taxonomy import ActivityLabel


class Scenario(BaseModel):
    """One deterministic completion or fault."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    fail_first: int = Field(default=0, ge=0)
    label: ActivityLabel = "fall"
    output: str | None = None
    delay_ms: int = Field(default=900, ge=0)
    fault: Literal[
        "none",
        "malformed",
        "invalid",
        "ambiguous",
        "truncated",
        "429",
        "500",
        "503",
        "interruption",
    ] = "none"


class Manifest(BaseModel):
    """Exact input and request mappings with a fixed fallback."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    model: str = "qwen3-vl-8b-instruct"
    default: Scenario = Scenario()
    inputs: dict[str, Scenario] = Field(default_factory=dict)
    requests: dict[str, Scenario] = Field(default_factory=dict)

    def identity(self) -> str:
        """Hash all settings that can affect a response."""
        return "mock-v1-sha256:" + fingerprint(self.model_dump())


def fingerprint(value: Any) -> str:
    """Hash canonical JSON without volatile transport identifiers."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def load_manifest() -> Manifest:
    """Load and validate effective startup settings, including environment overrides."""
    path = os.getenv("MOCK_INFERENCE_MANIFEST")
    raw = json.loads(Path(path).read_text()) if path else {}
    default = raw.setdefault("default", {})
    for env, field, convert in (
        ("MOCK_INFERENCE_LABEL", "label", str),
        ("MOCK_INFERENCE_DELAY_MS", "delay_ms", int),
    ):
        if env in os.environ:
            default[field] = convert(os.environ[env])
    if "MOCK_INFERENCE_MODEL" in os.environ:
        raw["model"] = os.environ["MOCK_INFERENCE_MODEL"]
    manifest = Manifest.model_validate(raw)
    return manifest


def next_attempt(path: Path, identity: str, request_key: str) -> int:
    """Atomically persist attempts only for deliberate fail-then-succeed fixtures."""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS attempts (identity TEXT, request_key TEXT, count INTEGER NOT NULL, PRIMARY KEY(identity, request_key))"
        )
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT count FROM attempts WHERE identity=? AND request_key=?", (identity, request_key)
        ).fetchone()
        count = (row[0] if row else 0) + 1
        connection.execute(
            "INSERT INTO attempts VALUES (?, ?, ?) ON CONFLICT(identity, request_key) DO UPDATE SET count=excluded.count",
            (identity, request_key, count),
        )
    return count
