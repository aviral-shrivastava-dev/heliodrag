"""Build the Starlink generation seed from GCAT.

The output is ``transform/seeds/starlink_generation_map.csv``, which dbt loads
and joins to the catalogue. It is committed because GCAT is CC-BY, so the seed
is reproducible by anyone without needing Space-Track credentials.

The classification itself lives in ``science.generations`` and is pure; this
module is the I/O and shaping around it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from starlink_drag.clients import gcat
from starlink_drag.science.generations import area_to_mass_proxy, classify

SEED_COLUMNS: tuple[str, ...] = (
    "norad_id",
    "jcat",
    "object_name",
    "bus",
    "generation",
    "label_source",
    "label_confidence",
    "launch_mass_kg",
    "dry_mass_kg",
    "span_m",
    "area_to_mass_proxy",
    "launch_date",
    "decay_date",
)


@dataclass(frozen=True, slots=True)
class SeedReport:
    """How completely and how confidently the catalogue was labelled."""

    rows: int
    labelled: int
    per_generation: pl.DataFrame

    @property
    def labelled_fraction(self) -> float:
        return self.labelled / self.rows if self.rows else 0.0

    def describe(self) -> str:
        lines = [
            f"{self.rows:,} Starlink payloads, "
            f"{self.labelled:,} labelled ({self.labelled_fraction:.1%})"
        ]
        for row in self.per_generation.iter_rows(named=True):
            lines.append(
                f"  {row['generation']:<12} {row['n']:>6,}  "
                f"{row['label_confidence']:<6} "
                f"median launch mass {row['median_launch_mass_kg'] or float('nan'):.0f} kg"
            )
        return "\n".join(lines)


def _gcat_date(column: str) -> pl.Expr:
    """Parse a GCAT date such as ``2019 May 24`` or ``2020 Oct  1 1300?``.

    Spacing is variable (single-digit days are space-padded), a time of day may
    follow, and an uncertain date carries a trailing ``?``. Only the calendar
    date is kept; the extra precision is not useful here and the qualifier is.
    """
    return (
        pl.col(column)
        .str.replace_all(r"\s+", " ")
        .str.extract(r"^(\d{4} [A-Za-z]{3} \d{1,2})")
        .str.to_date("%Y %b %d", strict=False)
    )


def build(frame: pl.DataFrame) -> pl.DataFrame:
    """Filter GCAT to Starlink payloads and attach a generation label."""
    starlink = frame.filter(
        pl.col("Name").str.starts_with("Starlink") & pl.col("Type").str.contains("P")
    )

    numeric = {
        "Mass": "launch_mass_kg",
        "DryMass": "dry_mass_kg",
        "Span": "span_m",
    }
    starlink = starlink.with_columns(
        [pl.col(src).cast(pl.Float64, strict=False).alias(dst) for src, dst in numeric.items()]
        + [
            pl.col("Satcat").cast(pl.Int64, strict=False).alias("norad_id"),
            _gcat_date("LDate").alias("launch_date"),
            _gcat_date("DDate").alias("decay_date"),
        ]
    ).filter(pl.col("norad_id").is_not_null())

    labels = [
        classify(bus, mass)
        for bus, mass in zip(
            starlink["Bus"].to_list(), starlink["launch_mass_kg"].to_list(), strict=True
        )
    ]
    proxies = [
        area_to_mass_proxy(span, dry)
        for span, dry in zip(
            starlink["span_m"].to_list(), starlink["dry_mass_kg"].to_list(), strict=True
        )
    ]

    return (
        starlink.with_columns(
            pl.Series("generation", [label.generation for label in labels]),
            pl.Series("label_source", [label.label_source for label in labels]),
            pl.Series("label_confidence", [label.confidence for label in labels]),
            pl.Series("area_to_mass_proxy", proxies, dtype=pl.Float64),
        )
        .rename({"JCAT": "jcat", "Name": "object_name", "Bus": "bus"})
        .select(SEED_COLUMNS)
        .unique(subset=["norad_id"], keep="first")
        # Round before writing: the proxy is a crude ratio and sixteen
        # significant figures triples the committed file size for no meaning.
        .with_columns(pl.col("area_to_mass_proxy").round(5))
        .sort("norad_id")
    )


def summarise(seed: pl.DataFrame) -> SeedReport:
    """Per-generation counts and medians, for eyeballing the banding."""
    per_generation = (
        seed.group_by("generation")
        .agg(
            pl.len().alias("n"),
            pl.col("label_confidence").first(),
            pl.col("launch_mass_kg").median().alias("median_launch_mass_kg"),
            pl.col("span_m").median().alias("median_span_m"),
            pl.col("launch_date").min().alias("first_launch"),
            pl.col("launch_date").max().alias("last_launch"),
        )
        .sort("generation")
    )
    labelled = int(seed.filter(pl.col("generation") != "unknown").height)
    return SeedReport(rows=seed.height, labelled=labelled, per_generation=per_generation)


def write_seed(seed: pl.DataFrame, destination: Path) -> Path:
    """Write the seed CSV that dbt loads."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    seed.write_csv(destination)
    return destination


def build_from_cache(cache_path: Path, destination: Path, *, refresh: bool = False) -> SeedReport:
    """Fetch (or reuse) GCAT, build the seed, write it, and report coverage."""
    path = gcat.fetch(cache_path, refresh=refresh)
    seed = build(gcat.load(path))
    write_seed(seed, destination)
    return summarise(seed)
