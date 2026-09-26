"""The estimators, validated on simulated data where the truth is known.

Simulated, and deliberately so: the question here is whether the method
recovers a sensitivity that was put in, detects a difference that exists and
does not invent one that does not. No number here is a scientific result.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from analysis.models.fit_drag_response import (
    FitSettings,
    descent_crosscheck,
    holm,
    storm_sensitivity,
)

FAST = FitSettings(bootstrap=400, min_satellite_storms=100)


def simulate(
    betas: dict[str, float], *, storms: int = 40, satellites: int = 60, seed: int = 7
) -> pl.DataFrame:
    """Satellite-storm responses: delta = beta * forcing + 5 * flux + noise."""
    rng = np.random.default_rng(seed)
    dst = -rng.uniform(50, 300, storms)
    flux = rng.uniform(100, 250, storms)
    rows = []
    for generation, beta in betas.items():
        storm_noise = rng.normal(0, 5, storms)
        for storm in range(storms):
            deltas = (
                beta * -dst[storm] / 100
                + 5 * (flux[storm] - 150) / 100
                + storm_noise[storm]
                + rng.normal(0, 20, satellites)
            )
            for index, delta in enumerate(deltas):
                rows.append(
                    {
                        "storm_id": storm,
                        "peak_dst_nt": int(dst[storm]),
                        "f10_7_sfu": float(flux[storm]),
                        "norad_id": hash(generation) % 1000 * 1000 + index,
                        "generation": generation,
                        "shell_km": 550.0,
                        "delta_m": float(delta),
                    }
                )
    return pl.DataFrame(rows)


def test_a_planted_sensitivity_is_recovered() -> None:
    sensitivity, _ = storm_sensitivity(simulate({"v1.5": -25.0, "v2-mini": -50.0}), FAST)

    for generation, truth in (("v1.5", -25.0), ("v2-mini", -50.0)):
        row = sensitivity.filter(pl.col("generation") == generation).row(0, named=True)
        assert row["beta_m_per_100nt"] == pytest.approx(truth, abs=5)
        assert row["storms"] == 40


def test_the_storm_interval_covers_the_truth_about_95_percent_of_the_time() -> None:
    """One interval missing is expected one time in twenty, so a single draw
    proves nothing: coverage is a rate, and it is tested as one."""
    settings = FitSettings(bootstrap=200, min_satellite_storms=100)
    covered = 0
    for seed in range(40):
        sensitivity, _ = storm_sensitivity(
            simulate({"v1.5": -25.0}, satellites=20, seed=seed), settings
        )
        row = sensitivity.row(0, named=True)
        covered += row["ci_low"] < -25.0 < row["ci_high"]

    assert covered / 40 >= 0.85, f"covered {covered} of 40"


def test_a_real_difference_is_detected() -> None:
    _, contrasts = storm_sensitivity(simulate({"v1.5": -25.0, "v2-mini": -50.0}), FAST)

    (row,) = contrasts.iter_rows(named=True)
    assert row["difference_m_per_100nt"] == pytest.approx(25.0, abs=6)
    assert row["ci_low"] > 0
    assert row["p_holm"] < 0.05


def test_no_difference_is_not_invented() -> None:
    _, contrasts = storm_sensitivity(simulate({"v1.5": -30.0, "v2-mini": -30.0}), FAST)

    (row,) = contrasts.iter_rows(named=True)
    assert row["ci_low"] < 0 < row["ci_high"]
    assert row["p_holm"] > 0.05


def test_generations_are_compared_on_the_storms_both_flew_through() -> None:
    responses = simulate({"v1.5": -25.0, "v2-mini": -50.0})
    later = responses.filter((pl.col("generation") == "v1.5") | (pl.col("storm_id") >= 20))

    _, contrasts = storm_sensitivity(later, FAST)

    assert contrasts["common_storms"].to_list() == [20]


def test_the_same_inputs_give_the_same_intervals() -> None:
    responses = simulate({"v1.5": -25.0, "v2-mini": -50.0})

    first = storm_sensitivity(responses, FAST)
    second = storm_sensitivity(responses.sample(fraction=1.0, shuffle=True, seed=3), FAST)

    assert first[0].equals(second[0])
    assert first[1].equals(second[1])


def test_adding_a_cell_does_not_move_another_cells_interval() -> None:
    """Each estimate has its own random stream, keyed by what it estimates."""
    alone, _ = storm_sensitivity(simulate({"v1.5": -25.0}), FAST)
    with_other, _ = storm_sensitivity(
        pl.concat([simulate({"v1.5": -25.0}), simulate({"v2-mini": -50.0}, seed=8)]), FAST
    )

    assert alone.equals(with_other.filter(pl.col("generation") == "v1.5"))


def test_small_cells_and_unanalysed_generations_are_left_out() -> None:
    responses = simulate({"v1.5": -25.0, "v0.9": -25.0}, storms=40)
    few_storms = simulate({"v2-mini": -50.0}, storms=10, seed=9)

    sensitivity, _ = storm_sensitivity(pl.concat([responses, few_storms]), FAST)

    assert sensitivity["generation"].to_list() == ["v1.5"]


def _descent_sums(seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for month in range(24):
        ap = rng.normal(0, 8, 200)
        flux = rng.normal(0, 10, 200)
        y = -3.0 * ap + 0.5 * flux + rng.normal(0, 15, 200)
        rows.append(
            {
                "generation": "v1.0", "shell_km": 350.0, "month": month, "n": 200,
                "s_aa": float(ap @ ap), "s_af": float(ap @ flux), "s_ff": float(flux @ flux),
                "s_ay": float(ap @ y), "s_fy": float(flux @ y), "s_yy": float(y @ y),
            }
        )  # fmt: skip
    return pl.DataFrame(rows)


def test_the_descent_regression_recovers_a_planted_ap_coefficient() -> None:
    (row,) = descent_crosscheck(_descent_sums(11), FAST).iter_rows(named=True)

    assert row["beta_m_per_10ap"] == pytest.approx(-30.0, abs=2)
    assert row["months"] == 24


def test_the_descent_interval_covers_the_truth_about_95_percent_of_the_time() -> None:
    settings = FitSettings(bootstrap=200)
    covered = 0
    for seed in range(40):
        (row,) = descent_crosscheck(_descent_sums(seed), settings).iter_rows(named=True)
        covered += row["ci_low"] < -30.0 < row["ci_high"]

    assert covered / 40 >= 0.85, f"covered {covered} of 40"


def test_holm_steps_down() -> None:
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])
