"""The figures, as functions from result tables to matplotlib figures.

Matplotlib rather than the explorer's Altair: a paper needs static files that
render the same everywhere, and Agg with the bundled DejaVu Sans font produces
PNGs that are identical byte for byte from run to run. Colours are the
explorer's colour-blind-checked palette (``starlink_drag.serving.charts``), so
a generation is the same colour on every chart in the project. One y-axis per
panel, always.
"""

from __future__ import annotations

from typing import Final

import matplotlib as mpl
import matplotlib.pyplot as plt
import polars as pl
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from analysis.models import data
from analysis.models.fit_drag_response import GENERATIONS
from starlink_drag.serving.charts import colour

# Files only, never a window: Agg renders the same bytes on every machine.
mpl.use("Agg")
mpl.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 100,
        "savefig.dpi": 150,
    }
)

ORDER: Final = GENERATIONS
NO_DATA: Final = "No estimate meets the minimum sample"


def _ordered(frame: pl.DataFrame) -> pl.DataFrame:
    rank = {g: i for i, g in enumerate(ORDER)}
    return (
        frame.with_columns(
            pl.col("generation").replace_strict(rank, default=len(ORDER)).alias("_rank")
        )
        # Stable, or rows that tie could swap between runs and change the bytes.
        .sort("_rank", maintain_order=True)
        .drop("_rank")
    )


def _empty(axes: Axes) -> None:
    axes.text(0.5, 0.5, NO_DATA, ha="center", va="center", transform=axes.transAxes)
    axes.set_axis_off()


def coverage(phase_days: pl.DataFrame, flux: pl.DataFrame) -> Figure:
    """Figure 1: which generations flew when, against the solar cycle."""
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(7.5, 5), sharex=False)
    top.plot(flux["month"].to_list(), flux["f10_7_sfu"].to_list(), color="#52514e")
    top.set_ylabel("F10.7, monthly mean (sfu)")
    top.set_title("Solar Cycle 25, and when each generation was operational", loc="left")
    operational = _ordered(
        phase_days.filter((pl.col("phase") == "operational") & pl.col("generation").is_in(ORDER))
    )
    generations = [g for g in ORDER if g in set(operational["generation"].to_list())]
    years = sorted(set(operational["year"].to_list()))
    width = 0.8 / max(len(generations), 1)
    for index, generation in enumerate(generations):
        rows = operational.filter(pl.col("generation") == generation)
        counts = dict(zip(rows["year"].to_list(), rows["satellite_days"].to_list(), strict=True))
        bottom.bar(
            [year + (index - len(generations) / 2 + 0.5) * width for year in years],
            [counts.get(year, 0) / 1000 for year in years],
            width=width,
            color=colour(generation, dark=False),
            label=generation,
        )
    bottom.set_ylabel("Operational satellite-days (thousands)")
    bottom.set_xticks(years)
    bottom.legend(ncols=3, frameon=False, fontsize=8)
    figure.tight_layout()
    return figure


def superposed_epoch(curves: pl.DataFrame, sensitivity: pl.DataFrame) -> Figure:
    """Figure 2: altitude rate relative to each satellite's own baseline, by day
    from the storm peak. Only the generation-shell cells the model estimates, in
    shells more than one of them shares: the figure shows the model's data."""
    cells = sensitivity.select("generation", "shell_km")
    shared = sorted(s for s, n in cells.group_by("shell_km").len().iter_rows() if n > 1)
    used = curves.join(cells, on=["generation", "shell_km"], how="semi")
    width = max(len(shared), 1)
    figure, panels = plt.subplots(1, width, figsize=(3.3 * width, 3.4), squeeze=False, sharey=True)
    if not shared:
        _empty(panels[0][0])
    for axes, shell in zip(panels[0], shared, strict=False):
        cell = used.filter(pl.col("shell_km") == shell)
        for generation in [g for g in ORDER if g in set(cell["generation"].to_list())]:
            rows = cell.filter(pl.col("generation") == generation).sort("day")
            axes.plot(
                rows["day"].to_list(),
                rows["relative_rate_m"].to_list(),
                marker="o",
                markersize=3,
                color=colour(generation, dark=False),
                label=generation,
            )
        axes.axvspan(*data.RESPONSE_DAYS, color="#e1e0d9", zorder=0)
        axes.axvspan(*data.BASELINE_DAYS, color="#f1f0ea", zorder=0)
        axes.axhline(0, color="#898781", linewidth=0.8)
        axes.set_title(f"{shell:.0f}-{shell + 25:.0f} km", loc="left")
        axes.set_xlabel("Days from storm peak")
        axes.legend(frameon=False, fontsize=8)
    panels[0][0].set_ylabel("Rate minus own baseline (m/day)")
    figure.suptitle(
        "Storm response, superposed on the peak (dark band: response, light: baseline)",
        x=0.01,
        ha="left",
        fontsize=9,
    )
    figure.tight_layout()
    return figure


def sensitivity_forest(estimates: pl.DataFrame) -> Figure:
    """Figure 3: storm sensitivity per generation and shell, with its interval."""
    shells = sorted(estimates["shell_km"].unique().to_list(), reverse=True)
    parts = [_ordered(estimates.filter(pl.col("shell_km") == shell)) for shell in shells]
    frame = pl.concat(parts) if parts else estimates
    figure, axes = plt.subplots(figsize=(7, 0.34 * max(frame.height, 4) + 1.3))
    if frame.is_empty():
        _empty(axes)
        return figure
    rows = list(range(frame.height))[::-1]
    for y, row in zip(rows, frame.iter_rows(named=True), strict=True):
        tint = colour(row["generation"], dark=False)
        _interval(axes, row["beta_m_per_100nt"], row["ci_low"], row["ci_high"], y, tint)
    axes.set_yticks(
        rows, [f"{r['shell_km']:.0f} km  {r['generation']}" for r in frame.iter_rows(named=True)]
    )
    axes.axvline(0, color="#898781", linewidth=0.8)
    axes.set_xlabel(
        "m/day of extra altitude change per 100 nT of peak Dst (negative: falls faster)"
    )
    axes.set_title("Storm sensitivity, 95% storm-bootstrap intervals", loc="left")
    figure.tight_layout()
    return figure


def contrasts(table: pl.DataFrame) -> Figure:
    """Figure 4: within-shell differences on common storms, Holm-adjusted p."""
    figure, axes = plt.subplots(figsize=(8, 0.38 * max(table.height, 4) + 1.3))
    if table.is_empty():
        _empty(axes)
        return figure
    rows = list(range(table.height))[::-1]
    for y, row in zip(rows, table.iter_rows(named=True), strict=True):
        value = row["difference_m_per_100nt"]
        axes.errorbar(
            value,
            y,
            xerr=[[value - row["ci_low"]], [row["ci_high"] - value]],
            fmt="o",
            mfc="#1a1a19" if row["p_holm"] < 0.05 else "white",
            color="#1a1a19",
            capsize=2,
        )
        axes.annotate(
            f"p = {row['p_holm']:.2g}",
            (row["ci_high"], y),
            xytext=(4, -3),
            textcoords="offset points",
            fontsize=7,
        )
    labels = [
        f"{r['shell_km']:.0f} km  {r['generation']} minus {r['versus']}"
        f" ({r['common_storms']} storms)"
        for r in table.iter_rows(named=True)
    ]
    axes.set_yticks(rows, labels)
    axes.axvline(0, color="#898781", linewidth=0.8)
    axes.set_xlabel("Difference in storm sensitivity on shared storms (m/day per 100 nT)")
    axes.set_title("Within-shell contrasts (filled: Holm-adjusted p < 0.05)", loc="left")
    figure.tight_layout()
    return figure


def area_to_mass(sensitivity: pl.DataFrame, generations: pl.DataFrame) -> Figure:
    """Figure 5: does storm sensitivity follow area per kilogram, within a shell?"""
    joined = sensitivity.join(generations.select("generation", "area_to_mass"), on="generation")
    figure, axes = plt.subplots(figsize=(6.5, 4))
    if joined.is_empty():
        _empty(axes)
        return figure
    shells = sorted(joined["shell_km"].unique().to_list())
    for index, shell in enumerate(shells):
        offset = (index - (len(shells) - 1) / 2) * 0.02  # shells side by side, not on top
        marker = "osD^vP<>"[index % 8]
        for row in joined.filter(pl.col("shell_km") == shell).iter_rows(named=True):
            _interval(
                axes,
                row["beta_m_per_100nt"],
                row["ci_low"],
                row["ci_high"],
                row["area_to_mass"] + offset,
                colour(row["generation"], dark=False),
                vertical=True,
                marker=marker,
            )
        axes.plot([], [], marker, color="#52514e", linestyle="none", label=f"{shell:.0f} km shell")
    axes.axhline(0, color="#898781", linewidth=0.8)
    axes.set_xlabel("Median area-to-mass proxy of the generation (GCAT)")
    axes.set_ylabel("Storm sensitivity (m/day per 100 nT)")
    axes.set_title("Storm sensitivity against area per kilogram (colour: generation)", loc="left")
    axes.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    return figure


def crosscheck(estimates: pl.DataFrame) -> Figure:
    """Figure 6: the descent cross-check against altitude, one series per generation."""
    figure, axes = plt.subplots(figsize=(7.5, 4))
    if estimates.is_empty():
        _empty(axes)
        return figure
    present = [g for g in ORDER if g in set(estimates["generation"].to_list())]
    for index, generation in enumerate(present):
        rows = estimates.filter(pl.col("generation") == generation).sort("shell_km")
        centre = 12.5 + (index - (len(present) - 1) / 2) * 3.0
        tint = colour(generation, dark=False)
        for row in rows.iter_rows(named=True):
            at = row["shell_km"] + centre
            _interval(
                axes, row["beta_m_per_10ap"], row["ci_low"], row["ci_high"], at, tint, vertical=True
            )
        axes.plot(
            [s + centre for s in rows["shell_km"].to_list()],
            rows["beta_m_per_10ap"].to_list(),
            color=tint,
            linewidth=0.8,
            label=generation,
        )
    axes.axhline(0, color="#898781", linewidth=0.8)
    axes.set_xlabel("Altitude shell, centre (km)")
    axes.set_ylabel("m/day per 10 nT of daily ap")
    axes.set_title(
        "Cross-check: satellites no longer station-keeping, 95% month-bootstrap", loc="left"
    )
    axes.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    return figure


def naive_versus_controlled(naive: pl.DataFrame, sensitivity: pl.DataFrame) -> Figure:
    """Figure 7: what the brief warns against, beside what the controls give."""
    figure, (left, right) = plt.subplots(1, 2, figsize=(9, 3.8))
    frame = _ordered(naive.filter(pl.col("generation").is_in(ORDER)))
    labels = frame["generation"].to_list()[::-1]
    left.barh(
        labels,
        frame["slope_m_per_100nt"].to_list()[::-1],
        color=[colour(g, dark=False) for g in labels],
    )
    left.set_title("Naive: raw daily rate against daily Dst", loc="left")
    controlled = _ordered(sensitivity)
    names = [f"{r['generation']}, {r['shell_km']:.0f} km" for r in controlled.iter_rows(named=True)]
    right.barh(
        names[::-1],
        controlled["beta_m_per_100nt"].to_list()[::-1],
        color=[colour(g, dark=False) for g in controlled["generation"].to_list()[::-1]],
    )
    right.set_title("Controlled: storm response, own baseline", loc="left")
    for axes in (left, right):
        axes.axvline(0, color="#898781", linewidth=0.8)
        axes.set_xlabel("m/day per 100 nT")
    figure.tight_layout()
    return figure


def _interval(
    axes: Axes,
    value: float,
    low: float,
    high: float,
    at: float,
    tint: str,
    *,
    vertical: bool = False,
    marker: str = "o",
) -> None:
    error = [[value - low], [high - value]]
    if vertical:
        axes.errorbar(at, value, yerr=error, fmt=marker, color=tint, capsize=2)
    else:
        axes.errorbar(value, at, xerr=error, fmt=marker, color=tint, capsize=2)
