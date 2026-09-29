"""Offline gate: the committed Phase 0 fixture set is complete, passed and pinned."""

import json
import re
from pathlib import Path

import pytest

from scripts.contract_smoke import DEFAULT_OUT, PROVENANCE, REQUIRED_FIXTURES

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / DEFAULT_OUT


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((FIXTURES / "manifest.json").read_text())


def test_manifest_all_passed(manifest: dict) -> None:
    failed = [c["name"] for c in manifest["checks"] if not c["ok"]]
    assert manifest["all_passed"] is True, failed


def test_required_fixtures_present() -> None:
    names = [p.name.split("_", 1)[1].removesuffix(".json") for p in FIXTURES.glob("[0-9]*.json")]
    for required in REQUIRED_FIXTURES:
        assert names.count(required) == 1, required


def test_every_fixture_is_labelled_simulated() -> None:
    for path in FIXTURES.glob("[0-9]*.json"):
        assert json.loads(path.read_text())["provenance"] == PROVENANCE, path.name


def test_image_digest_matches_compose(manifest: dict) -> None:
    digest = re.search(r"sha256:[0-9a-f]{64}", manifest["image_digest"] or "")
    assert digest is not None
    assert digest.group(0) in (ROOT / "docker-compose.yml").read_text()


def test_replay_status_recorded(manifest: dict) -> None:
    assert manifest["findings"]["replay_status"] in (200, 201)
