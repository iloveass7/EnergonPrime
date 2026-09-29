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


def test_cli_refuses_compare_against_its_own_output_dir(tmp_path) -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--out", str(tmp_path), "--compare-to", str(tmp_path)])
    assert exc.value.code == 2


def test_cli_refuses_compare_with_default_out() -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--compare-to", str(smoke.DEFAULT_OUT)])
    assert exc.value.code == 2


def test_cli_refuses_partial_run_into_committed_fixtures() -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--section", "preflight"])
    assert exc.value.code == 2
