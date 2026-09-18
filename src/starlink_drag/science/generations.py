"""Starlink hardware generations. Pure functions, no I/O.

Every result in this project is stratified by the label this module produces, so
it records not just *what* each satellite is but *how confidently* that is known.
A label inferred from mass is not the same evidence as one the source states
outright, and the analysis in Phase 7 needs to be able to tell them apart.

Source
------
Jonathan McDowell's General Catalog of Artificial Space Objects (GCAT), which is
the only public catalogue carrying a per-satellite spacecraft-bus field together
with mass and span. Space-Track and CelesTrak carry neither, which is why they
cannot answer the generation question alone.

    McDowell, J. C., "General Catalog of Artificial Space Objects",
    https://planet4589.org/space/gcat -- licensed CC-BY.

Because GCAT is CC-BY rather than restricted, the derived seed *can* be
committed and republished with attribution, unlike anything from Space-Track.

Label provenance
----------------
The Gen2 buses are stated by GCAT explicitly in its ``Bus`` column and are
labelled ``stated``.

The Gen1 buses are all recorded as plain ``Starlink``; GCAT does not distinguish
v0.9 from v1.0 from v1.5. Those are separated here by launch-mass banding and
labelled ``inferred_mass`` -- a weaker claim, deliberately marked as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

UNKNOWN: Final = "unknown"

BUS_TO_GENERATION: Final[dict[str, str]] = {
    "Starlink V2M": "v2-mini",
    "Starlink V2MO": "v2-mini-opt",
    "Starlink V2MD": "v2-mini-dtc",
}
"""Gen2 buses, stated outright by GCAT."""

GEN1_BUS: Final = "Starlink"

GEN1_MASS_BANDS_KG: Final[tuple[tuple[float, float, str], ...]] = (
    (0.0, 240.0, "v0.9"),
    (240.0, 280.0, "v1.0"),
    (280.0, 10_000.0, "v1.5"),
)
"""Launch-mass bands separating the Gen1 buses. Upper bound exclusive.

These band on **launch** mass, not dry mass. Dry mass is unusable: GCAT records a
v1.5 sub-variant at 220 kg dry, colliding with v0.9's 219 kg, and others at
260-275 kg, colliding with v1.0's 248 kg. Launch mass is cleanly trimodal for
Gen1 -- roughly 227 / 260 / 290-305 -- with no overlap between adjacent versions.
"""

GENERATIONS: Final[tuple[str, ...]] = (
    "v0.9",
    "v1.0",
    "v1.5",
    "v2-mini",
    "v2-mini-opt",
    "v2-mini-dtc",
    UNKNOWN,
)
"""Every label this module can emit. Marts test against exactly this list."""

CONFIDENCE_BY_SOURCE: Final[dict[str, str]] = {
    "stated": "high",
    "inferred_mass": "medium",
    "no_mass": "none",
    "unmatched_bus": "none",
}


@dataclass(frozen=True, slots=True)
class GenerationLabel:
    """A generation, and the evidence behind it."""

    generation: str
    label_source: str

    @property
    def confidence(self) -> str:
        return CONFIDENCE_BY_SOURCE.get(self.label_source, "none")

    @property
    def is_known(self) -> bool:
        return self.generation != UNKNOWN


def classify(bus: str | None, launch_mass_kg: float | None) -> GenerationLabel:
    """Label one satellite from its GCAT bus and launch mass.

    Returns ``unknown`` rather than guessing when the evidence is absent. A
    satellite labelled by mistake is worse than one left out: the research
    question is a comparison *between* generations, so a mislabelled satellite
    contaminates two groups at once.
    """
    if bus in BUS_TO_GENERATION:
        return GenerationLabel(BUS_TO_GENERATION[bus], "stated")

    if bus == GEN1_BUS:
        if launch_mass_kg is None:
            return GenerationLabel(UNKNOWN, "no_mass")
        for low, high, generation in GEN1_MASS_BANDS_KG:
            if low <= launch_mass_kg < high:
                return GenerationLabel(generation, "inferred_mass")

    return GenerationLabel(UNKNOWN, "unmatched_bus")


def area_to_mass_proxy(span_m: float | None, dry_mass_kg: float | None) -> float | None:
    """A crude area-to-mass ratio, ``span^2 / dry_mass``.

    **This is not a ballistic coefficient and must not be reported as one.**

    Span is the deployed solar-array wingspan, not a drag cross-section.
    Starlink flies knife-edge on station and open-book when deorbiting, so the
    true drag area is attitude-dependent and appears in no public catalogue.
    This quantity is useful only for comparing generations *relative to each
    other* under a fixed geometric assumption.
    """
    if span_m is None or dry_mass_kg is None or dry_mass_kg <= 0 or span_m <= 0:
        return None
    return span_m * span_m / dry_mass_kg


def is_gen2(generation: str) -> bool:
    """Whether a label belongs to the second-generation bus family."""
    return generation.startswith("v2-")
