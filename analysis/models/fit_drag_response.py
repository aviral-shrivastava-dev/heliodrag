"""Per-generation drag response to space weather, with its confounders controlled.

Two estimates, identified differently, so that agreement means something
(docs/phases/phase-7.md has the full argument):

**Storm sensitivity (the main result).** Operational satellites station-keep:
their thrusters cancel drag, so their raw decay says nothing about the air. A
storm raises the air's density faster than the thrusters respond, so each
satellite's rate in the two days after a storm's peak, minus its own rate in
the quiet days before, is a drag signal. Per generation and home shell::

    delta[satellite, storm] = a + beta * forcing[storm] + lambda * flux[storm] + e

with ``forcing`` the storm's peak -Dst in units of 100 nT and ``flux`` its F10.7
(centred on 150 sfu, per 100 sfu). The three confounders the brief names are
each handled explicitly:

- *altitude shell*: every estimate is within one 25 km shell, and generations
  are compared only within a shell they share;
- *orbit raising*: raising and descending satellites are excluded; each
  satellite is its own control, before and after, within its operational phase;
- *the solar-cycle trend*: removed by differencing against the satellite's own
  baseline days earlier, and the storm's solar flux enters the model.

``beta`` is m/day of extra altitude change per 100 nT of storm; negative means
falling faster. Uncertainty is a cluster bootstrap over **storms** -- every
satellite in a storm saw the same forcing, so storms, not satellites, are the
independent draws. Generations are compared on the storms both flew through,
with the same resampled storms for both, so a contrast is between responses to
*the same forcing*.

**Descent cross-check.** Satellites no longer station-keeping (see ``data``),
regressed day by day: within each satellite-month the rate, ap and F10.7 are
detrended, then the rate's variation is regressed on ap's with F10.7 alongside.
Bootstrap over calendar months. Different satellites, forcing measure and
identification: if the generations order the same way, the ordering is not an
artefact of either method.
"""

from __future__ import annotations

import itertools
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import polars as pl
from numpy.typing import NDArray

from analysis.models import data

SOLAR_FLUX_CENTRE: Final = 150.0
GENERATIONS: Final = ("v1.0", "v1.5", "v2-mini", "v2-mini-dtc", "v2-mini-opt")
"""The research question's four, and v2-mini-opt (the largest generation, which
the question predates). v0.9, sixty 2019 prototypes, is not analysed."""
Float = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class FitSettings:
    bootstrap: int = 2000
    seed: int = 20260926
    confidence: float = 0.95
    min_storms: int = 15
    min_satellite_storms: int = 1000
    """Below this a "home shell" is not an operational shell -- v1.0 satellites
    whose most-observed shell is 325 km, say -- and the estimate is noise."""
    min_months: int = 12
    min_satellite_days: int = 500


@dataclass(frozen=True, slots=True)
class _Cell:
    storms: NDArray[np.int64]
    weight: Float
    delta: Float
    forcing: Float
    flux: Float

    def restricted(self, keep: NDArray[np.int64]) -> _Cell:
        mask = np.isin(self.storms, keep)
        return _Cell(
            self.storms[mask],
            self.weight[mask],
            self.delta[mask],
            self.forcing[mask],
            self.flux[mask],
        )

    def slope(self, draw: NDArray[np.int64] | None = None) -> tuple[float, float]:
        """(beta, intercept) by weighted least squares on storm means; ``draw``
        picks storms by position, with repeats, for the bootstrap."""
        rows = np.arange(self.storms.size) if draw is None else draw
        design = np.column_stack([np.ones(rows.size), self.forcing[rows], self.flux[rows]])
        root = np.sqrt(self.weight[rows])
        solution = np.linalg.lstsq(design * root[:, None], self.delta[rows] * root, rcond=None)[0]
        return float(solution[1]), float(solution[0])


def storm_sensitivity(
    responses: pl.DataFrame, settings: FitSettings | None = None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(one row per generation and shell, one row per within-shell contrast)."""
    settings = settings or FitSettings()
    cells = _storm_cells(responses.filter(pl.col("generation").is_in(GENERATIONS)), settings)
    rows = []
    for (generation, shell), cell in sorted(cells.items()):
        beta, intercept = cell.slope()
        draws = _bootstrap(cell, _rng(settings, generation, shell), settings)
        low, high = _interval(draws, settings)
        rows.append(
            {
                "generation": generation, "shell_km": shell, "storms": cell.storms.size,
                "satellite_storms": int(cell.weight.sum()), "beta_m_per_100nt": beta,
                "ci_low": low, "ci_high": high, "intercept_m": intercept,
            }
        )  # fmt: skip
    return _round(pl.DataFrame(rows, schema=_SENSITIVITY)), _contrasts(cells, settings)


def descent_crosscheck(sums: pl.DataFrame, settings: FitSettings | None = None) -> pl.DataFrame:
    """ap coefficient per generation and shell, in m/day per 10 nT of ap."""
    settings = settings or FitSettings()
    rows = []
    analysed = sums.filter(pl.col("generation").is_in(GENERATIONS))
    for (generation, shell), cell in analysed.group_by(["generation", "shell_km"]):
        months = cell.sort("month")
        if months.height < settings.min_months or months["n"].sum() < settings.min_satellite_days:
            continue
        stats = months.select("s_aa", "s_af", "s_ff", "s_ay", "s_fy").to_numpy()
        rng = _rng(settings, str(generation), float(shell), "descent")
        draws = [
            _ap_coefficient(stats[rng.integers(0, months.height, months.height)].sum(axis=0))
            for _ in range(settings.bootstrap)
        ]
        low, high = _interval(np.asarray(draws), settings)
        rows.append(
            {
                "generation": generation, "shell_km": shell, "months": months.height,
                "satellite_days": int(months["n"].sum()),
                "beta_m_per_10ap": _ap_coefficient(stats.sum(axis=0)),
                "ci_low": low, "ci_high": high,
            }
        )  # fmt: skip
    return _round(pl.DataFrame(rows, schema=_CROSSCHECK)).sort(["shell_km", "generation"])


def holm(p_values: list[float]) -> list[float]:
    """Holm's step-down adjustment: many contrasts are tested at once."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted, running = [0.0] * len(p_values), 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def _storm_cells(responses: pl.DataFrame, settings: FitSettings) -> dict[tuple[str, float], _Cell]:
    per_storm = (
        responses.group_by(["generation", "shell_km", "storm_id"])
        .agg(
            pl.len().alias("n"),
            pl.col("delta_m").mean(),
            pl.col("peak_dst_nt").first(),
            pl.col("f10_7_sfu").first(),
        )
        .sort(["generation", "shell_km", "storm_id"])
    )
    cells = {}
    for (generation, shell), group in per_storm.group_by(["generation", "shell_km"]):
        if group.height < settings.min_storms or group["n"].sum() < settings.min_satellite_storms:
            continue
        group = group.sort("storm_id").with_columns(pl.col("delta_m").round_sig_figs(10))
        cells[(str(generation), float(shell))] = _Cell(
            storms=group["storm_id"].to_numpy(),
            weight=group["n"].to_numpy().astype(np.float64),
            delta=group["delta_m"].to_numpy(),
            forcing=-group["peak_dst_nt"].to_numpy() / 100.0,
            flux=(group["f10_7_sfu"].to_numpy() - SOLAR_FLUX_CENTRE) / 100.0,
        )
    return cells


def _contrasts(cells: dict[tuple[str, float], _Cell], settings: FitSettings) -> pl.DataFrame:
    rows = []
    for (first, shell), (second, other_shell) in itertools.combinations(sorted(cells), 2):
        if shell != other_shell:
            continue
        common = np.intersect1d(cells[(first, shell)].storms, cells[(second, shell)].storms)
        if common.size < settings.min_storms:
            continue
        a, b = cells[(first, shell)].restricted(common), cells[(second, shell)].restricted(common)
        rng = _rng(settings, first, shell, second)
        draws = np.empty(settings.bootstrap)
        for i in range(settings.bootstrap):
            pick = rng.integers(0, common.size, common.size)  # the same storms for both
            draws[i] = a.slope(pick)[0] - b.slope(pick)[0]
        low, high = _interval(draws, settings)
        # Two-sided, and never exactly zero: with B draws the smallest honest
        # p is about 2 / (B + 1).
        extreme = min(int((draws <= 0).sum()), int((draws >= 0).sum()))
        p_value = min(1.0, 2.0 * (extreme + 1) / (settings.bootstrap + 1))
        rows.append(
            {
                "shell_km": shell, "generation": first, "versus": second,
                "common_storms": common.size, "difference_m_per_100nt": a.slope()[0] - b.slope()[0],
                "ci_low": low, "ci_high": high, "p_bootstrap": p_value,
            }
        )  # fmt: skip
    frame = pl.DataFrame(rows, schema=_CONTRASTS)
    frame = frame.with_columns(pl.Series("p_holm", holm(frame["p_bootstrap"].to_list())))
    return _round(frame).sort(["shell_km", "generation", "versus"])


def _bootstrap(cell: _Cell, rng: np.random.Generator, settings: FitSettings) -> Float:
    size = cell.storms.size
    return np.array([cell.slope(rng.integers(0, size, size))[0] for _ in range(settings.bootstrap)])


def _ap_coefficient(stats: Float) -> float:
    s_aa, s_af, s_ff, s_ay, s_fy = stats
    solution = np.linalg.solve(np.array([[s_aa, s_af], [s_af, s_ff]]), np.array([s_ay, s_fy]))
    return float(solution[0]) * 10.0


def _interval(draws: Float, settings: FitSettings) -> tuple[float, float]:
    tail = (1.0 - settings.confidence) / 2.0 * 100.0
    low, high = np.percentile(draws, [tail, 100.0 - tail])
    return float(low), float(high)


def _rng(settings: FitSettings, *key: object) -> np.random.Generator:
    """One stream per estimate, keyed by what it estimates, so adding or
    removing a cell never changes another cell's interval."""
    return np.random.default_rng([settings.seed, zlib.crc32(repr(key).encode())])


def _round(frame: pl.DataFrame) -> pl.DataFrame:
    floats = [name for name, dtype in frame.schema.items() if dtype.is_float()]
    return frame.with_columns(pl.col(floats).round_sig_figs(6))


_SENSITIVITY: Final = {
    "generation": pl.Utf8, "shell_km": pl.Float64, "storms": pl.Int64,
    "satellite_storms": pl.Int64, "beta_m_per_100nt": pl.Float64, "ci_low": pl.Float64,
    "ci_high": pl.Float64, "intercept_m": pl.Float64,
}  # fmt: skip
_CONTRASTS: Final = {
    "shell_km": pl.Float64, "generation": pl.Utf8, "versus": pl.Utf8, "common_storms": pl.Int64,
    "difference_m_per_100nt": pl.Float64, "ci_low": pl.Float64, "ci_high": pl.Float64,
    "p_bootstrap": pl.Float64,
}  # fmt: skip
_CROSSCHECK: Final = {
    "generation": pl.Utf8, "shell_km": pl.Float64, "months": pl.Int64,
    "satellite_days": pl.Int64, "beta_m_per_10ap": pl.Float64, "ci_low": pl.Float64,
    "ci_high": pl.Float64,
}  # fmt: skip


def main(database: Path = Path("data/atlas.duckdb")) -> None:
    """Print the estimates. ``python -m analysis.make_figures`` writes them."""
    con = data.connect(database)
    sensitivity, contrasts = storm_sensitivity(data.storm_responses(con))
    with pl.Config(tbl_rows=100, tbl_cols=20, tbl_width_chars=200):
        print(sensitivity, contrasts, descent_crosscheck(data.descent_sums(con)), sep="\n")


if __name__ == "__main__":
    main()
