"""Entrypoint: build the generation mapping, validate it, and report.

    python -m starlink_drag.build [--refresh]

Writes `data/interim/starlink_generation_map.parquet` and prints a coverage
report plus the epoch-overlap matrix that determines which generation
comparisons are scientifically defensible.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import pandas as pd

from . import generation_map as gm

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW_CACHE = PROJECT_ROOT / "data" / "raw" / "gcat_satcat.tsv"
OUTPUT = PROJECT_ROOT / "data" / "interim" / "starlink_generation_map.parquet"


def epoch_overlap(mapping: pd.DataFrame) -> pd.DataFrame:
    """Months during which each pair of generations was simultaneously on orbit.

    Generation is almost perfectly collinear with launch epoch, and launch epoch is
    collinear with solar-cycle phase. Any comparison between two generations is
    therefore only interpretable over the window where both were flying at once.
    This matrix says how wide that window is for each pair.
    """
    generations = sorted(g for g in mapping["generation"].unique() if g != "unknown")
    spans = {}
    for generation in generations:
        subset = mapping[mapping["generation"] == generation]
        start = subset["launch_date"].min()
        # A generation stops being observable once effectively all of it has decayed.
        decayed = subset["decay_date"].dropna()
        end = (
            decayed.quantile(0.95)
            if subset["is_decayed"].mean() > 0.95
            else pd.Timestamp.today()
        )
        spans[generation] = (start, end)

    matrix = pd.DataFrame(index=generations, columns=generations, dtype="object")
    for a in generations:
        for b in generations:
            start = max(spans[a][0], spans[b][0])
            end = min(spans[a][1], spans[b][1])
            months = max(0, round((end - start).days / 30.44))
            matrix.loc[a, b] = months
    return matrix


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-download GCAT")
    args = parser.parse_args(argv)

    path = gm.fetch_gcat(RAW_CACHE, refresh=args.refresh)
    mapping = gm.build(gm.load_gcat(path))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    mapping.to_parquet(OUTPUT, index=False)

    report = gm.coverage(mapping)
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        print(f"\nStarlink payloads: {report.total_rows}")
        print(f"Labelled: {report.labelled} ({report.labelled_fraction:.2%})  "
              f"unknown: {report.unknown}  missing NORAD: {report.norad_missing}")
        print("\n--- per generation ---")
        print(report.per_generation.to_string())
        print("\n--- months both generations simultaneously on orbit ---")
        print(epoch_overlap(mapping).to_string())

    failures = gm.validate(mapping)
    if failures:
        print("\nVALIDATION FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"\nAll validation checks passed. Wrote {OUTPUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
