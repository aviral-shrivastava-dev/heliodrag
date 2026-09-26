"""Does the storm result depend on the windows chosen for it?

The main windows -- baseline days -6..-2, response 0..+2 -- were fixed before
any result was seen. These variants re-run the whole storm analysis with each
window moved, and are published whatever they show: a result that holds only
for one window is a result about the window.
"""

from __future__ import annotations

from typing import Final

import duckdb
import polars as pl

from analysis.models import data
from analysis.models.fit_drag_response import FitSettings, storm_sensitivity

VARIANTS: Final[dict[str, tuple[data.Window, data.Window]]] = {
    "main (baseline -6..-2, response 0..+2)": (data.BASELINE_DAYS, data.RESPONSE_DAYS),
    "shorter response (0..+1)": (data.BASELINE_DAYS, (0, 1)),
    "longer response (0..+3)": (data.BASELINE_DAYS, (0, 3)),
    "earlier baseline (-8..-3)": ((-8, -3), data.RESPONSE_DAYS),
}


def window_variants(
    con: duckdb.DuckDBPyConnection, settings: FitSettings | None = None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(sensitivity, contrasts) under every variant, with a ``variant`` column."""
    sensitivities, contrasts = [], []
    for label, (baseline, response) in VARIANTS.items():
        responses = data.storm_responses(con, baseline, response)
        sensitivity, contrast = storm_sensitivity(responses, settings)
        sensitivities.append(sensitivity.select(pl.lit(label).alias("variant"), pl.all()))
        contrasts.append(contrast.select(pl.lit(label).alias("variant"), pl.all()))
    return pl.concat(sensitivities), pl.concat(contrasts)
