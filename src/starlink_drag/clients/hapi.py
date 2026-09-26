"""NASA OMNI client, via the SPDF HAPI server.

Public, unauthenticated, no rate limit. Two things about this server are
load-bearing and easy to get wrong:

1. **Parameters must be requested in the dataset's own order.** Asking for
   ``Time,F10,AP,DST`` when the dataset declares ``Time,F10,KP,DST,AP`` returns
   HAPI error 1411, not the data. The order is read from ``/info``.
2. **Errors arrive with HTTP 200, and are not valid JSON.** A failed ``/data``
   request returns a stray comma, then a status object, then a closing brace --
   which parses as neither CSV nor JSON. Checking ``response.status_code``
   alone silently yields zero rows; trying ``response.json()`` raises.

Fill values are converted to ``None`` here rather than downstream. OMNI encodes
"no measurement" as 999.9 for F10.7 and 99999 for Dst; letting those through as
numbers would put a fake 999.9 solar flux into the science.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from functools import lru_cache
from types import TracebackType
from typing import Any, Self

import httpx

from starlink_drag.config import HapiSettings

OMNI_PARAMETERS = ("F10_INDEX1800", "KP1800", "DST1800", "AP_INDEX1800")
"""The four space-weather drivers, named as OMNI2_H0_MRG1HR declares them."""


class HapiError(RuntimeError):
    """The HAPI server refused a request or returned something unusable."""


class HapiClient:
    """Reads OMNI space-weather parameters from a HAPI server."""

    def __init__(self, settings: HapiSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            follow_redirects=True,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- metadata ----------------------------------------------------------

    def info(self, dataset: str | None = None) -> dict[str, Any]:
        """Dataset metadata: parameter order, types and fill values."""
        dataset = dataset or self._settings.dataset
        response = self._client.get("/info", params={"id": dataset})
        if response.status_code != 200:
            raise HapiError(f"HAPI /info returned {response.status_code} for {dataset}")
        payload: dict[str, Any] = response.json()
        _raise_for_hapi_status(payload)
        return payload

    def ordered_parameters(self, wanted: tuple[str, ...], dataset: str | None = None) -> list[str]:
        """Return ``wanted`` sorted into the dataset's declared order.

        Always includes ``Time`` first, which HAPI requires.
        """
        declared = [p["name"] for p in self.info(dataset)["parameters"]]
        missing = set(wanted) - set(declared)
        if missing:
            raise HapiError(f"dataset does not declare {sorted(missing)}")
        keep = {*wanted, "Time"}
        return [name for name in declared if name in keep]

    def fill_values(self, dataset: str | None = None) -> dict[str, str]:
        """Per-parameter fill markers, as the server spells them."""
        return {
            p["name"]: str(p["fill"])
            for p in self.info(dataset)["parameters"]
            if p.get("fill") is not None
        }

    # -- data --------------------------------------------------------------

    def omni(
        self,
        start: dt.date,
        end: dt.date,
        *,
        parameters: tuple[str, ...] = OMNI_PARAMETERS,
        dataset: str | None = None,
    ) -> list[dict[str, Any]]:
        """Hourly OMNI records over ``[start, end)``.

        Returned as delivered, one row per hour, with fills as ``None``. Daily
        aggregation belongs in the transformation layer, not here -- bronze
        keeps what the server sent.
        """
        dataset = dataset or self._settings.dataset
        ordered = self.ordered_parameters(parameters, dataset)
        fills = self.fill_values(dataset)

        response = self._client.get(
            "/data",
            params={
                "id": dataset,
                "parameters": ",".join(ordered),
                "time.min": _as_hapi_time(start),
                "time.max": _as_hapi_time(end),
                "format": "csv",
            },
        )
        if response.status_code != 200:
            raise HapiError(f"HAPI /data returned {response.status_code}")

        text = response.text
        if _raise_for_csv_error(text) == HAPI_NO_DATA:
            return []
        return _parse_csv(text, ordered, fills)


@lru_cache(maxsize=8)
def _numeric_fill(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_csv(text: str, columns: list[str], fills: dict[str, str]) -> list[dict[str, Any]]:
    """Parse HAPI CSV into typed rows, mapping fill markers to ``None``."""
    rows: list[dict[str, Any]] = []
    for record in csv.reader(text.splitlines()):
        if not record:
            continue
        if len(record) != len(columns):
            raise HapiError(f"expected {len(columns)} columns, got {len(record)}: {record[:4]}")
        row: dict[str, Any] = {}
        for name, value in zip(columns, record, strict=True):
            row[name] = _coerce(name, value.strip(), fills)
        rows.append(row)
    return rows


def _coerce(name: str, value: str, fills: dict[str, str]) -> Any:
    if name == "Time":
        return value
    if value == "":
        return None
    parsed = _numeric_fill(value)
    if parsed is None:
        return None
    fill = fills.get(name)
    if fill is not None:
        fill_value = _numeric_fill(fill)
        if fill_value is not None and parsed == fill_value:
            return None
    return parsed


def _as_hapi_time(value: dt.date) -> str:
    return f"{value.isoformat()}T00:00:00Z"


_STATUS_RE = re.compile(
    r'"status"\s*:\s*\{[^}]*?"code"\s*:\s*(?P<code>\d+)'
    r'[^}]*?"message"\s*:\s*"(?P<message>[^"]*)"'
)


HAPI_OK = 1200
HAPI_NO_DATA = 1201
"""HAPI's "OK - no data for time range": a success with nothing in it, not an
error. NASA publishes OMNI about a week behind real time, so every recent day
answers with this -- and treating it as a failure made the daily run fail every
day, until a run inside Docker on 2026-09-26 asked for yesterday."""


def _raise_for_csv_error(text: str) -> int:
    """Detect a failure reported inside a nominally-CSV body.

    A failed CSV request does not return JSON. The observed shape is a stray
    comma, then a status object, then a closing brace -- which parses as neither
    CSV nor JSON:

        ,
        "status": {"code": 1411, "message": "HAPI error 1411: ..."}
        }

    So the marker is searched for rather than the body being parsed. Returns
    the status code: 1200, or 1201 when the range simply holds no data.
    """
    if '"status"' not in text:
        return HAPI_OK
    match = _STATUS_RE.search(text)
    if match is None:
        raise HapiError(f"HAPI returned an error body: {text[:200]}")
    code = int(match.group("code"))
    if code not in (HAPI_OK, HAPI_NO_DATA):
        raise HapiError(f"HAPI status {code}: {match.group('message')}")
    return code


def _raise_for_hapi_status(payload: dict[str, Any]) -> None:
    status = payload.get("status", {})
    code = status.get("code")
    if code is not None and int(code) != 1200:
        raise HapiError(f"HAPI status {code}: {status.get('message')}")
