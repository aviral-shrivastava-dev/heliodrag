"""NASA OMNI space-weather loader via the SPDF HAPI server.

OMNI supplies the drivers the drag analysis regresses against: F10.7 solar radio
flux, and the Kp / Ap / Dst geomagnetic indices, hourly from 1963 to present.

Unlike Space-Track this needs no authentication and imposes no published rate
limit, so the loader is a straightforward chunked pull. It is still partitioned
by year and marked with _SUCCESS for the same reason: re-running must be cheap
and safe.

Cite as: King, J. H. and Papitashvili, N. E., OMNI 1-hour data,
NASA SPDF, doi:10.48322/1shr-ht18
"""

from __future__ import annotations

import datetime as dt
import functools
import io
import logging

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from .. import config

log = logging.getLogger(__name__)

BRONZE_OMNI = config.BRONZE / "omni"

# Fill values live in config, read from the server's own /info response. Bronze
# preserves them verbatim so the raw layer stays faithful to what was returned.
FILL_VALUES = config.OMNI_FILL_VALUES


@retry(wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(5), reraise=True)
def _get(endpoint: str, params: dict[str, str]) -> requests.Response:
    response = requests.get(
        f"{config.OMNI_HAPI_BASE}/{endpoint}",
        params=params,
        timeout=300,
        headers={"User-Agent": "starlink-drag-atlas/0.1 (academic research)"},
    )
    response.raise_for_status()
    return response


@functools.lru_cache(maxsize=1)
def dataset_parameter_order() -> tuple[str, ...]:
    """Parameter names in the order the dataset itself declares them.

    HAPI rejects a `parameters` list that is not in dataset order with
    "HAPI error 1411: Parameter out of order". Reading the order from the server
    rather than hardcoding it keeps the loader working if OMNI is ever revised.
    """
    payload = _get("info", {"id": config.OMNI_DATASET}).json()
    return tuple(p["name"] for p in payload["parameters"])


def ordered_parameters() -> list[str]:
    """Our requested parameters, sorted into the dataset's declared order."""
    order = dataset_parameter_order()
    unknown = set(config.OMNI_PARAMETERS) - set(order)
    if unknown:
        raise RuntimeError(f"parameters absent from {config.OMNI_DATASET}: {sorted(unknown)}")
    return sorted(config.OMNI_PARAMETERS, key=order.index)


def fetch_range(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Pull hourly OMNI records for [start, end)."""
    parameters = ordered_parameters()
    response = _get("data", {
        "id": config.OMNI_DATASET,
        "parameters": ",".join(parameters),
        "time.min": f"{start.isoformat()}T00:00:00Z",
        "time.max": f"{end.isoformat()}T00:00:00Z",
        "format": "csv",
    })
    payload = response.text

    # HAPI signals failure in the body with HTTP 200, so status alone is not enough.
    if payload.lstrip().startswith(("{", ",")) and '"status"' in payload[:400]:
        raise RuntimeError(f"HAPI returned an error: {payload[:200]}")
    if not payload.strip():
        return pd.DataFrame(columns=["time", *parameters])

    frame = pd.read_csv(io.StringIO(payload), header=None, names=["time", *parameters])
    frame["time"] = pd.to_datetime(frame["time"], format="ISO8601", utc=True)
    return frame


def backfill(start: dt.date, end: dt.date, refresh: bool = False) -> dict[str, int]:
    """Load OMNI year by year into bronze, skipping years already complete."""
    config.ensure_layers()
    BRONZE_OMNI.mkdir(parents=True, exist_ok=True)

    summary = {"years_fetched": 0, "rows": 0, "years_skipped": 0}
    for year in range(start.year, end.year + 1):
        directory = BRONZE_OMNI / f"year={year}"
        if (directory / "_SUCCESS").exists() and not refresh:
            summary["years_skipped"] += 1
            continue

        window_start = max(start, dt.date(year, 1, 1))
        window_end = min(end, dt.date(year + 1, 1, 1))
        if window_start >= window_end:
            continue

        frame = fetch_range(window_start, window_end)
        directory.mkdir(parents=True, exist_ok=True)
        staging = directory / "data.parquet.tmp"
        frame.to_parquet(staging, index=False)
        staging.replace(directory / "data.parquet")
        (directory / "_SUCCESS").write_text(
            f"{dt.datetime.now(dt.timezone.utc).isoformat()}\t{len(frame)}\n",
            encoding="utf-8",
        )

        summary["years_fetched"] += 1
        summary["rows"] += len(frame)
        log.info("OMNI %d: %d rows", year, len(frame))

    return summary
