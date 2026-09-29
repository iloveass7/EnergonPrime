"""CLI section selection: a typo must fail loudly, not run nothing."""

import pytest

import scripts.contract_smoke as smoke


def test_cli_rejects_unknown_section() -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--section", "no-such-section"])
    assert exc.value.code == 2


def test_sections_run_in_documented_order() -> None:
    names = [name for name, _ in smoke.SECTIONS]
    assert names[:2] == ["preflight", "reads"]
