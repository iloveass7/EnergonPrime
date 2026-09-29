"""Determinism compare between two recorded fixture directories."""

import json
from pathlib import Path

from scripts.contract_smoke import compare_dirs


def _fixture(name: str, body: object, *, volatile: bool = False, **extra: object) -> dict:
    return {
        "name": name,
        "volatile": volatile,
        "response": {"status": 200, "headers": extra.get("headers", {}), "body": body},
        "elapsed_ms": extra.get("elapsed_ms", 1.0),
    }


def _sse(events: list[tuple[str, object]], keepalives: int = 0) -> dict:
    lines = [": connected", ""] + [": keepalive", ""] * keepalives
    return {
        "name": "sse_stream",
        "segments": [
            {
                "status": 200,
                "lines": lines,
                "events": [{"event": e, "data": d} for e, d in events],
            },
            {"status": 503, "lines": ['{"detail":{}}'], "events": []},
        ],
    }


def _write(directory: Path, docs: list[dict]) -> Path:
    directory.mkdir()
    for seq, doc in enumerate(docs, start=1):
        (directory / f"{seq:03d}_{doc['name']}.json").write_text(json.dumps(doc))
    return directory


def test_compare_dirs_ignores_volatile_and_wall_time(tmp_path) -> None:
    a = _write(
        tmp_path / "a",
        [
            _fixture(
                "audit",
                [{"id": 1, "wall_time": "t1"}],
                headers={"content-length": "40"},
            ),
            _fixture("admin_run", {"tick": 3}, volatile=True),
            _sse([("simulation.tick", {"tick": 1})]),
        ],
    )
    b = _write(
        tmp_path / "b",
        [
            _fixture(
                "audit",
                [{"id": 1, "wall_time": "t2.5"}],
                headers={"content-length": "42"},
                elapsed_ms=9.0,
            ),
            _fixture("admin_run", {"tick": 9}, volatile=True),
            _sse([("simulation.tick", {"tick": 1})], keepalives=2),
        ],
    )
    assert compare_dirs(a, b) == []


def test_compare_dirs_reports_changed_body(tmp_path) -> None:
    a = _write(tmp_path / "a", [_fixture("alloc_create", {"quantity": 1000})])
    b = _write(tmp_path / "b", [_fixture("alloc_create", {"quantity": 999})])
    diffs = compare_dirs(a, b)
    assert len(diffs) == 1 and "alloc_create" in diffs[0]


def test_compare_dirs_reports_changed_sse_sequence(tmp_path) -> None:
    a = _write(tmp_path / "a", [_sse([("simulation.tick", {"tick": 1})])])
    b = _write(tmp_path / "b", [_sse([("simulation.tick", {"tick": 2})])])
    assert [d.split(":")[0] for d in compare_dirs(a, b)] == ["sse_stream"]


def test_compare_dirs_reports_missing_fixture(tmp_path) -> None:
    a = _write(tmp_path / "a", [_fixture("health", {}), _fixture("routes", [])])
    b = _write(tmp_path / "b", [_fixture("health", {})])
    assert compare_dirs(a, b) == ["fixture set/order differs: missing ['routes'], extra []"]
