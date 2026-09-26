"""Phase 7's acceptance, on the synthetic fixture warehouse: every output is
written by one command, and writing it again gives the same bytes.

The fixture has ten days and one storm, far below the minimum sample, so every
estimate is empty -- which also proves each figure draws its "no estimate"
state rather than failing on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis import make_figures
from analysis.models.fit_drag_response import FitSettings

pytestmark = pytest.mark.integration

FAST = FitSettings(bootstrap=50)
EXPECTED = {
    "storms.csv",
    "storm_sensitivity.csv",
    "generation_contrasts.csv",
    "descent_crosscheck.csv",
    "naive_slopes.csv",
    "robustness_sensitivity.csv",
    "robustness_contrasts.csv",
    *(f"fig{n}_{name}.png" for n, name in enumerate(
        ["coverage", "superposed_epoch", "storm_sensitivity", "generation_contrasts",
         "sensitivity_vs_area_to_mass", "descent_crosscheck", "naive_vs_controlled"], start=1)),
}  # fmt: skip


def test_one_command_writes_every_output_and_their_checksums(
    built_warehouse: Path, tmp_path: Path
) -> None:
    hashes = make_figures.build(built_warehouse, tmp_path / "figures", FAST)

    assert set(hashes) == EXPECTED
    listed = (tmp_path / "figures" / make_figures.CHECKSUMS).read_text(encoding="utf-8")
    assert all(f"{digest}  {name}" in listed for name, digest in hashes.items())
    carriage_return = bytes([13])
    for text in [make_figures.CHECKSUMS, *(n for n in EXPECTED if n.endswith(".csv"))]:
        content = (tmp_path / "figures" / text).read_bytes()
        assert carriage_return not in content, f"{text}: CRLF, so not the same bytes as on Linux"


def test_deleting_the_outputs_and_rebuilding_restores_them_byte_for_byte(
    built_warehouse: Path, tmp_path: Path
) -> None:
    first = make_figures.build(built_warehouse, tmp_path / "first", FAST)
    second = make_figures.build(built_warehouse, tmp_path / "second", FAST)

    assert first == second


def test_check_passes_on_fresh_outputs_and_names_a_changed_one(
    built_warehouse: Path, tmp_path: Path
) -> None:
    output = tmp_path / "figures"
    make_figures.build(built_warehouse, output, FAST)
    assert make_figures.check(built_warehouse, output, FAST) == []

    # As if the committed storms.csv were not what the code now produces.
    checksums = output / make_figures.CHECKSUMS
    lines = [
        "0" * 64 + "  storms.csv" if line.endswith("  storms.csv") else line
        for line in checksums.read_text(encoding="utf-8").splitlines()
    ]
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert make_figures.check(built_warehouse, output, FAST) == ["storms.csv"]


def test_the_command_refuses_without_a_warehouse(tmp_path: Path) -> None:
    code = make_figures.main(
        ["--database", str(tmp_path / "none.duckdb"), "--output", str(tmp_path)]
    )

    assert code == 2
