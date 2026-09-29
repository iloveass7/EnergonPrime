"""Offline tests for the Phase 0 contract smoke helpers (no simulator needed)."""

import json
import re

import httpx
import pytest

from scripts.contract_smoke import (
    PROVENANCE,
    Exchange,
    FixtureWriter,
    envelope_code,
    is_stale,
    normalize,
)


def _exchange(name: str, body: object = None, body_text: str | None = None) -> Exchange:
    return Exchange(
        name=name,
        section="test",
        method="GET",
        path="/v1/instance",
        query={},
        req_body=None,
        status=200,
        headers={"content-type": "application/json", "x-simulator-stale": None},
        body=body,
        body_text=body_text,
        elapsed_ms=1.0,
        sim_tick=0,
        volatile=False,
    )


def test_envelope_code_domain() -> None:
    assert (
        envelope_code({"detail": {"code": "ROUTE_MISMATCH", "message": "x"}})
        == "ROUTE_MISMATCH"
    )


def test_envelope_code_fault() -> None:
    assert (
        envelope_code({"error": {"code": "FAULT_INJECTED", "message": "x"}})
        == "FAULT_INJECTED"
    )


def test_envelope_code_validation() -> None:
    assert envelope_code({"detail": [{"loc": ["body", "quantity"]}]}) == "VALIDATION"
    assert envelope_code({"id": 1}) is None
    assert envelope_code([{"id": 1}]) is None
    assert envelope_code(None) is None


def test_is_stale_case_insensitive() -> None:
    assert is_stale(httpx.Headers({"X-Simulator-Stale": "True"})) is True
    assert is_stale(httpx.Headers({})) is False
    assert is_stale(httpx.Headers({"x-simulator-stale": "false"})) is False


def test_normalize_drops_volatile_keys_at_any_depth() -> None:
    fixture = {
        "name": "admin_audit",
        "elapsed_ms": 12.5,
        "response": {
            "body": [
                {"id": 1, "wall_time": "2026-01-01T00:00:00Z", "tick": 3},
                {
                    "id": 2,
                    "nested": {"start_wall_time": "a", "end_wall_time": "b", "keep": 1},
                },
            ]
        },
    }
    assert normalize(fixture) == {
        "name": "admin_audit",
        "response": {"body": [{"id": 1, "tick": 3}, {"id": 2, "nested": {"keep": 1}}]},
    }


def test_writer_rejects_duplicate_name(tmp_path) -> None:
    writer = FixtureWriter(tmp_path)
    writer.write(_exchange("instance"))
    with pytest.raises(ValueError, match="instance"):
        writer.write(_exchange("instance"))


def test_writer_stores_non_json_body_as_text(tmp_path) -> None:
    writer = FixtureWriter(tmp_path)
    writer.write(_exchange("first", body={"ok": True}))
    path = writer.write(_exchange("admin_html", body=None, body_text="<html>"))
    assert re.fullmatch(r"\d{3}_admin_html\.json", path.name)
    assert path.name == "002_admin_html.json"
    data = json.loads(path.read_text())
    assert data["provenance"] == PROVENANCE
    assert data["response"]["body"] is None
    assert data["response"]["body_text"] == "<html>"
    assert data["response"]["headers"]["x-simulator-stale"] is None
    assert [entry["name"] for entry in writer.index] == ["first", "admin_html"]


def test_git_meta_flags_dirty_tree() -> None:
    from scripts.contract_smoke import git_meta

    outputs = {"rev-parse": "abc123", "status": " M scripts/contract_smoke.py"}

    def runner(cmd: list[str]) -> str | None:
        return outputs[cmd[1]]

    assert git_meta(runner) == {"git_sha": "abc123", "git_dirty": True}
    outputs["status"] = None
    assert git_meta(runner) == {"git_sha": "abc123", "git_dirty": False}
