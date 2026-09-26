"""Regenerate every figure and results table from the gold marts. One command:

    uv run python -m analysis.make_figures

Everything in ``analysis/figures/`` is written by this and by nothing else, so
deleting the directory and running it again restores it -- identically, byte
for byte, which ``--check`` verifies without touching the committed files:

    uv run python -m analysis.make_figures --check

``SHA256SUMS`` lists every output's hash, so a changed figure shows in review.
The outputs are aggregates per generation, shell and storm: derived products,
publishable under ADR-0007. No element set leaves the warehouse.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

import matplotlib.pyplot as plt
import polars as pl
from matplotlib.figure import Figure

from analysis import plots
from analysis.models import data
from analysis.models import fit_drag_response as fit
from analysis.models.robustness import window_variants

OUTPUT: Final = Path(__file__).resolve().parent / "figures"
DATABASE: Final = Path("data") / "atlas.duckdb"
CHECKSUMS: Final = "SHA256SUMS"


def build(database: Path, output: Path, settings: fit.FitSettings | None = None) -> dict[str, str]:
    """Write every table and figure to ``output``. Returns {file name: sha256}."""
    output.mkdir(parents=True, exist_ok=True)
    con = data.connect(database)
    sensitivity, contrasts = fit.storm_sensitivity(data.storm_responses(con), settings)
    crosscheck = fit.descent_crosscheck(data.descent_sums(con), settings)
    variant_sensitivity, variant_contrasts = window_variants(con, settings)
    naive = data.naive_slopes(con).filter(pl.col("generation").is_in(fit.GENERATIONS))

    tables = {
        "storms.csv": data.storms(con),
        "storm_sensitivity.csv": sensitivity,
        "generation_contrasts.csv": contrasts,
        "descent_crosscheck.csv": crosscheck,
        "naive_slopes.csv": naive,
        "robustness_sensitivity.csv": variant_sensitivity,
        "robustness_contrasts.csv": variant_contrasts,
    }
    figures: dict[str, Callable[[], Figure]] = {
        "fig1_coverage.png": lambda: plots.coverage(data.phase_days(con), data.solar_flux(con)),
        "fig2_superposed_epoch.png": lambda: plots.superposed_epoch(
            data.epoch_curves(con), sensitivity
        ),
        "fig3_storm_sensitivity.png": lambda: plots.sensitivity_forest(sensitivity),
        "fig4_generation_contrasts.png": lambda: plots.contrasts(contrasts),
        "fig5_sensitivity_vs_area_to_mass.png": lambda: plots.area_to_mass(
            sensitivity, data.generations(con)
        ),
        "fig6_descent_crosscheck.png": lambda: plots.crosscheck(crosscheck),
        "fig7_naive_vs_controlled.png": lambda: plots.naive_versus_controlled(naive, sensitivity),
    }

    for name, table in tables.items():
        table.write_csv(output / name)
    for name, draw in figures.items():
        figure = draw()
        # No "Software" chunk: it changes with every matplotlib release and
        # carries nothing a reader needs.
        figure.savefig(output / name, metadata={"Software": None})
        plt.close(figure)

    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir())
        if path.name != CHECKSUMS
    }
    (output / CHECKSUMS).write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8",
        newline="\n",  # text mode would write CRLF on Windows: not the same bytes as on Linux
    )
    return hashes


def check(database: Path, output: Path, settings: fit.FitSettings | None = None) -> list[str]:
    """Regenerate into a scratch directory; return the files that differ."""
    committed = _read_checksums(output / CHECKSUMS)
    with tempfile.TemporaryDirectory() as scratch:
        fresh = build(database, Path(scratch), settings)
    names = sorted(set(committed) | set(fresh))
    return [name for name in names if committed.get(name) != fresh.get(name)]


def _read_checksums(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    pairs = (line.split("  ", 1) for line in path.read_text(encoding="utf-8").splitlines())
    return {name: digest for digest, name in pairs}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="compare, do not write")
    arguments = parser.parse_args(argv)
    if not arguments.database.exists():
        print(f"no warehouse at {arguments.database}: build it first (make build)")
        return 2
    if arguments.check:
        differing = check(arguments.database, arguments.output)
        print("identical" if not differing else f"differ: {', '.join(differing)}")
        return 1 if differing else 0
    hashes = build(arguments.database, arguments.output)
    print(f"wrote {len(hashes)} files to {arguments.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
