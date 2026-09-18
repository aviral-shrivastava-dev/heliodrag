"""Build a NORAD ID -> Starlink hardware generation mapping table.

This is the foundational join for the Differential Drag Atlas. Every downstream
result is stratified by the `generation` column produced here, so this module is
deliberately conservative: it records *how* each label was derived and flags the
ones that are inferred rather than stated by the source.

Source
------
Jonathan McDowell's General Catalog of Artificial Space Objects (GCAT),
`satcat.tsv`. Licensed CC-BY; cite as McDowell, J. C., "General Catalog of
Artificial Space Objects", https://planet4589.org/space/gcat

GCAT is the only public source that carries a per-satellite spacecraft-bus field
*and* mass/span. CelesTrak and Space-Track carry neither, which is why they
cannot answer the generation question on their own.

Label provenance
----------------
The Gen2 buses are stated explicitly by GCAT in the `Bus` column
(``Starlink V2M`` / ``V2MO`` / ``V2MD``) and are labelled ``stated``.

The Gen1 buses are *all* recorded as plain ``Starlink``; GCAT does not
distinguish v0.9 / v1.0 / v1.5. Those are separated here by launch-mass banding
and labelled ``inferred_mass``. The banding is validated against two independently
known facts (see `validate`): v0.9 was a single batch of exactly 60 satellites
launched in 2019, and v1.0 production ran to roughly 1,700 units.
"""

from __future__ import annotations

import dataclasses
import io
import pathlib
import urllib.request

import pandas as pd

GCAT_SATCAT_URL = "https://planet4589.org/space/gcat/tsv/cat/satcat.tsv"
USER_AGENT = "starlink-drag-atlas/0.1 (academic research)"

# Column order is not stable across GCAT revisions, so we parse the header.
WANTED = [
    "JCAT", "Satcat", "Launch_Tag", "Piece", "Type", "Name", "LDate",
    "DDate", "Status", "Bus", "Mass", "DryMass", "Span", "Diameter",
    "Perigee", "Apogee", "Inc",
]

# Launch-mass bands (kg) separating the Gen1 buses. Upper bound is exclusive.
#
# These band on `Mass` (launch mass), NOT `DryMass`. Dry mass is unusable here:
# GCAT records one v1.5 sub-variant at 220 kg dry, which collides with v0.9's
# 219 kg, and other v1.5 variants at 260-275 kg, which collide with v1.0's
# 248 kg. Launch mass is cleanly trimodal for Gen1 -- 227 / 260 / 290-305 --
# with no overlap between adjacent versions.
GEN1_MASS_BANDS = [
    (0, 240, "v0.9"),
    (240, 280, "v1.0"),
    (280, 10_000, "v1.5"),
]

BUS_TO_GENERATION = {
    "Starlink V2M": "v2-mini",
    "Starlink V2MO": "v2-mini-opt",
    "Starlink V2MD": "v2-mini-dtc",
}


@dataclasses.dataclass(frozen=True)
class CoverageReport:
    """Summary of how completely and how confidently the catalogue was labelled."""

    total_rows: int
    labelled: int
    unknown: int
    norad_missing: int
    per_generation: pd.DataFrame

    @property
    def labelled_fraction(self) -> float:
        return self.labelled / self.total_rows if self.total_rows else 0.0


def fetch_gcat(cache_path: pathlib.Path, refresh: bool = False) -> pathlib.Path:
    """Download GCAT satcat.tsv, caching to `cache_path`.

    GCAT is ~19 MB and regenerated daily. We cache rather than re-fetch so that a
    re-run is reproducible and does not hammer a personal server.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and not refresh:
        return cache_path

    request = urllib.request.Request(GCAT_SATCAT_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        payload = response.read()
    cache_path.write_bytes(payload)
    return cache_path


def load_gcat(path: pathlib.Path) -> pd.DataFrame:
    """Parse GCAT's TSV, which carries its header on a '#'-prefixed first line."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    header = lines[0].lstrip("#").split("\t")
    body = [line for line in lines[1:] if not line.startswith("#")]

    frame = pd.read_csv(
        io.StringIO("\n".join(body)),
        sep="\t",
        names=header,
        dtype=str,
        engine="python",
        on_bad_lines="skip",
    )
    frame.columns = [c.strip() for c in frame.columns]
    frame = frame[[c for c in WANTED if c in frame.columns]]
    return frame.apply(lambda s: s.str.strip() if s.dtype == object else s)


def _classify_row(bus: str, launch_mass: float) -> tuple[str, str]:
    """Return (generation, label_source) for one satellite."""
    if bus in BUS_TO_GENERATION:
        return BUS_TO_GENERATION[bus], "stated"

    if bus == "Starlink":
        if pd.isna(launch_mass):
            return "unknown", "no_mass"
        for low, high, generation in GEN1_MASS_BANDS:
            if low <= launch_mass < high:
                return generation, "inferred_mass"

    return "unknown", "unmatched_bus"


def build(frame: pd.DataFrame) -> pd.DataFrame:
    """Filter GCAT to Starlink payloads and attach a generation label."""
    starlink = frame[
        frame["Name"].str.startswith("Starlink", na=False)
        & frame["Type"].str.contains("P", na=False)
    ].copy()

    numeric_columns = (
        ("Mass", "mass_kg"),
        ("DryMass", "dry_mass_kg"),
        ("Span", "span_m"),
        ("Perigee", "perigee_km"),
        ("Apogee", "apogee_km"),
        ("Inc", "inclination_deg"),
    )
    for source, target in numeric_columns:
        starlink[target] = pd.to_numeric(starlink[source], errors="coerce")

    labels = [
        _classify_row(bus, mass)
        for bus, mass in zip(starlink["Bus"], starlink["mass_kg"])
    ]
    starlink["generation"] = [generation for generation, _ in labels]
    starlink["label_source"] = [source for _, source in labels]
    starlink["label_confidence"] = (
        starlink["label_source"]
        .map({"stated": "high", "inferred_mass": "medium"})
        .fillna("none")
    )

    starlink["norad_id"] = pd.to_numeric(starlink["Satcat"], errors="coerce").astype("Int64")
    starlink["launch_date"] = pd.to_datetime(
        starlink["LDate"], format="mixed", errors="coerce"
    )
    starlink["decay_date"] = pd.to_datetime(
        starlink["DDate"].str.rstrip("?"), format="mixed", errors="coerce"
    )
    starlink["is_decayed"] = starlink["decay_date"].notna()

    # Span is the deployed solar-array WINGSPAN, not a drag cross-section. Starlink
    # flies low-drag ("knife edge") on station and high-drag ("open book") when
    # deorbiting, so true drag area is attitude-dependent and appears in no public
    # catalogue. This column compares generations *relative to each other* under a
    # fixed geometric assumption; it is not an absolute ballistic coefficient.
    starlink["crude_amr_proxy_span2_over_mass"] = (
        starlink["span_m"] ** 2 / starlink["dry_mass_kg"]
    )

    renamed = starlink.rename(columns={
        "JCAT": "jcat",
        "Launch_Tag": "launch_tag",
        "Piece": "cospar_piece",
        "Name": "name",
        "Bus": "bus",
        "Status": "status",
    })
    columns = [
        "norad_id", "jcat", "name", "cospar_piece", "launch_tag", "bus",
        "generation", "label_source", "label_confidence",
        "mass_kg", "dry_mass_kg", "span_m", "crude_amr_proxy_span2_over_mass",
        "launch_date", "decay_date", "is_decayed", "status",
        "perigee_km", "apogee_km", "inclination_deg",
    ]
    present = [c for c in columns if c in renamed.columns]
    return renamed[present].sort_values("norad_id").reset_index(drop=True)


def coverage(mapping: pd.DataFrame) -> CoverageReport:
    """Summarise label completeness and the epoch span of each generation."""
    per_generation = mapping.groupby("generation").agg(
        n=("norad_id", "size"),
        decayed=("is_decayed", "sum"),
        mass_kg=("mass_kg", "median"),
        dry_mass_kg=("dry_mass_kg", "median"),
        span_m=("span_m", "median"),
        amr_proxy=("crude_amr_proxy_span2_over_mass", "median"),
        first_launch=("launch_date", "min"),
        last_launch=("launch_date", "max"),
        confidence=("label_confidence", "first"),
    )
    per_generation["decay_pct"] = (
        100 * per_generation["decayed"] / per_generation["n"]
    ).round(1)

    return CoverageReport(
        total_rows=len(mapping),
        labelled=int((mapping["generation"] != "unknown").sum()),
        unknown=int((mapping["generation"] == "unknown").sum()),
        norad_missing=int(mapping["norad_id"].isna().sum()),
        per_generation=per_generation.sort_values("first_launch"),
    )


def validate(mapping: pd.DataFrame) -> list[str]:
    """Check the mapping against independently known facts.

    Returns a list of human-readable failures; empty means all checks passed.
    These assertions catch a silently-wrong join, which is the single biggest
    correctness risk in the project.
    """
    failures: list[str] = []

    v09 = mapping[mapping["generation"] == "v0.9"]
    if len(v09) != 60:
        failures.append(
            f"v0.9 should be exactly 60 satellites (single 2019 batch), got {len(v09)}"
        )
    if not v09.empty and v09["launch_date"].dt.year.nunique() != 1:
        failures.append("v0.9 should come from a single launch year")
    if not v09.empty and not v09["is_decayed"].all():
        failures.append("all v0.9 satellites should have reentered")

    n_v10 = int((mapping["generation"] == "v1.0").sum())
    if not 1_500 <= n_v10 <= 1_900:
        failures.append(f"v1.0 population should be ~1,700, got {n_v10}")

    # The Gen1/Gen2 split must be unambiguous in span: 9 m vs 29 m.
    gen1 = mapping[mapping["generation"].isin(["v0.9", "v1.0", "v1.5"])]["span_m"].dropna()
    gen2 = mapping[mapping["generation"].str.startswith("v2", na=False)]["span_m"].dropna()
    if not gen1.empty and not gen2.empty and gen1.max() >= gen2.min():
        failures.append(f"Gen1 span ({gen1.max()}) overlaps Gen2 span ({gen2.min()})")

    unknown_fraction = (mapping["generation"] == "unknown").mean()
    if unknown_fraction > 0.01:
        failures.append(f"{unknown_fraction:.1%} of satellites unlabelled (threshold 1%)")

    missing_norad = mapping["norad_id"].isna().mean()
    if missing_norad > 0.01:
        failures.append(f"{missing_norad:.1%} of rows lack a NORAD ID (threshold 1%)")

    return failures
