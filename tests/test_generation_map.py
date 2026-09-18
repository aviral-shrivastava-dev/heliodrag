"""Tests for the generation mapping.

The `validate` suite in `generation_map` runs against the live catalogue and is
the real guard on correctness. These tests pin the classification *logic* so a
refactor cannot silently change how a satellite is labelled.
"""

from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from starlink_drag import generation_map as gm  # noqa: E402

PARQUET = pathlib.Path(__file__).resolve().parents[1] / "data" / "interim" / "starlink_generation_map.parquet"


@pytest.mark.parametrize(
    ("bus", "mass", "expected_generation", "expected_source"),
    [
        ("Starlink V2M", 730, "v2-mini", "stated"),
        ("Starlink V2MO", 575, "v2-mini-opt", "stated"),
        ("Starlink V2MD", 960, "v2-mini-dtc", "stated"),
        ("Starlink", 227, "v0.9", "inferred_mass"),
        ("Starlink", 260, "v1.0", "inferred_mass"),
        ("Starlink", 300, "v1.5", "inferred_mass"),
        ("Starlink", 305, "v1.5", "inferred_mass"),
        ("-", 100, "unknown", "unmatched_bus"),
        ("Starlink", float("nan"), "unknown", "no_mass"),
    ],
)
def test_classify(bus, mass, expected_generation, expected_source):
    generation, source = gm._classify_row(bus, mass)
    assert (generation, source) == (expected_generation, expected_source)


def test_band_boundaries_are_exclusive_at_the_top():
    """240 and 280 belong to the *upper* band, not the lower one."""
    assert gm._classify_row("Starlink", 239.9)[0] == "v0.9"
    assert gm._classify_row("Starlink", 240)[0] == "v1.0"
    assert gm._classify_row("Starlink", 279.9)[0] == "v1.0"
    assert gm._classify_row("Starlink", 280)[0] == "v1.5"


def test_stated_bus_beats_mass():
    """An explicit Gen2 bus label must win even if the mass looks like Gen1."""
    assert gm._classify_row("Starlink V2M", 260)[0] == "v2-mini"


@pytest.fixture(scope="module")
def mapping():
    return pd.read_parquet(PARQUET)


@pytest.mark.skipif(not PARQUET.exists(), reason="run `python -m starlink_drag.build` first")
class TestBuiltArtifact:
    def test_live_validation_passes(self, mapping):
        assert gm.validate(mapping) == []

    def test_norad_ids_unique(self, mapping):
        present = mapping["norad_id"].dropna()
        assert present.is_unique

    def test_gen2_share_identical_span(self, mapping):
        """The V2 Mini natural experiment depends on geometry being held constant."""
        gen2 = mapping[mapping["generation"].str.startswith("v2", na=False)]
        assert gen2["span_m"].nunique() == 1

    def test_decayed_implies_decay_date(self, mapping):
        assert (mapping.loc[mapping["is_decayed"], "decay_date"].notna()).all()

    def test_decay_never_precedes_launch(self, mapping):
        decayed = mapping[mapping["is_decayed"]]
        assert (decayed["decay_date"] >= decayed["launch_date"]).all()
