"""Generation labelling, and the evidence behind each label.

Every result in the project is stratified by this label, so a mistake here
contaminates two groups at once -- the one a satellite is wrongly put in and the
one it is taken out of. These tests pin both the classification and the
provenance that says how much to trust it.
"""

from __future__ import annotations

import itertools

import pytest

from starlink_drag.science.generations import (
    GEN1_MASS_BANDS_KG,
    GENERATIONS,
    UNKNOWN,
    area_to_mass_proxy,
    classify,
    is_gen2,
)

# -- stated labels ---------------------------------------------------------


@pytest.mark.parametrize(
    ("bus", "expected"),
    [
        ("Starlink V2M", "v2-mini"),
        ("Starlink V2MO", "v2-mini-opt"),
        ("Starlink V2MD", "v2-mini-dtc"),
    ],
)
def test_gen2_buses_are_stated_by_the_source(bus: str, expected: str) -> None:
    label = classify(bus, launch_mass_kg=800.0)

    assert label.generation == expected
    assert label.label_source == "stated"
    assert label.confidence == "high"


def test_a_stated_bus_ignores_mass_entirely() -> None:
    """The bus is direct evidence; mass is only a fallback for Gen1."""
    assert classify("Starlink V2M", launch_mass_kg=None).generation == "v2-mini"
    assert classify("Starlink V2M", launch_mass_kg=1.0).generation == "v2-mini"


# -- inferred labels -------------------------------------------------------


@pytest.mark.parametrize(
    ("mass", "expected"),
    [
        (227.0, "v0.9"),  # the 2019 prototype batch
        (239.9, "v0.9"),
        (240.0, "v1.0"),  # band edges are exclusive at the top
        (260.0, "v1.0"),
        (279.9, "v1.0"),
        (280.0, "v1.5"),
        (300.0, "v1.5"),
    ],
)
def test_gen1_is_separated_by_launch_mass(mass: float, expected: str) -> None:
    label = classify("Starlink", launch_mass_kg=mass)

    assert label.generation == expected
    assert label.label_source == "inferred_mass"
    assert label.confidence == "medium"


def test_the_mass_bands_do_not_overlap_or_leave_gaps() -> None:
    edges = [(low, high) for low, high, _ in GEN1_MASS_BANDS_KG]

    for (_, high), (next_low, _) in itertools.pairwise(edges):
        assert high == next_low, "bands must meet exactly"


# -- refusing to guess -----------------------------------------------------


def test_a_gen1_bus_without_mass_is_unknown_rather_than_guessed() -> None:
    label = classify("Starlink", launch_mass_kg=None)

    assert label.generation == UNKNOWN
    assert label.label_source == "no_mass"
    assert label.confidence == "none"
    assert label.is_known is False


@pytest.mark.parametrize("bus", ["-", "", "Starshield", None, "Something Else"])
def test_an_unrecognised_bus_is_unknown(bus: str | None) -> None:
    label = classify(bus, launch_mass_kg=260.0)

    assert label.generation == UNKNOWN
    assert label.label_source == "unmatched_bus"


def test_a_gen1_mass_outside_every_band_is_unknown() -> None:
    """A 20-tonne "Starlink" is a catalogue error, not a new generation."""
    assert classify("Starlink", launch_mass_kg=20_000.0).generation == UNKNOWN


def test_every_emitted_label_is_declared() -> None:
    """The marts test generation against this list, so it must be complete."""
    emitted = {
        classify("Starlink V2M", 800.0).generation,
        classify("Starlink V2MO", 575.0).generation,
        classify("Starlink V2MD", 960.0).generation,
        classify("Starlink", 227.0).generation,
        classify("Starlink", 260.0).generation,
        classify("Starlink", 300.0).generation,
        classify(None, None).generation,
    }

    assert emitted == set(GENERATIONS)


# -- families --------------------------------------------------------------


@pytest.mark.parametrize("generation", ["v2-mini", "v2-mini-opt", "v2-mini-dtc"])
def test_gen2_family_is_recognised(generation: str) -> None:
    assert is_gen2(generation)


@pytest.mark.parametrize("generation", ["v0.9", "v1.0", "v1.5", UNKNOWN])
def test_gen1_and_unknown_are_not_gen2(generation: str) -> None:
    assert not is_gen2(generation)


# -- the area-to-mass proxy ------------------------------------------------


def test_the_proxy_is_span_squared_over_dry_mass() -> None:
    assert area_to_mass_proxy(9.0, 219.0) == pytest.approx(81.0 / 219.0)


def test_gen2_has_a_higher_proxy_than_gen1() -> None:
    """The physical premise of the research question: Gen2 is much bigger for
    its mass, so it should feel drag differently."""
    gen1 = area_to_mass_proxy(span_m=9.0, dry_mass_kg=260.0)
    gen2 = area_to_mass_proxy(span_m=29.0, dry_mass_kg=740.0)

    assert gen1 is not None and gen2 is not None
    assert gen2 > gen1


@pytest.mark.parametrize(
    ("span", "mass"), [(None, 200.0), (9.0, None), (9.0, 0.0), (0.0, 200.0), (9.0, -5.0)]
)
def test_the_proxy_is_none_when_it_cannot_be_computed(
    span: float | None, mass: float | None
) -> None:
    assert area_to_mass_proxy(span, mass) is None
