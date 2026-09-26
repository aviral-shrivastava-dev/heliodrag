"""Land Space-Track general-perturbations elements into bronze.

The fetch strategy is dictated by the rate limit. Asking for one day at a time
would need one request per NORAD batch per day -- tens of thousands of requests
for a single year, which at fewer than 300 an hour is weeks of wall clock.
Asking for a wide epoch range per batch instead needs a few hundred, because
Space-Track imposes no row cap on the response.

So elements are fetched wide and partitioned narrow: one request covers many
days for many satellites, and the rows are split into daily partitions on write.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from starlink_drag.clients.spacetrack import SpaceTrackClient, batched
from starlink_drag.config import Settings
from starlink_drag.ingest.bronze import GP_SPEC, land
from starlink_drag.schemas import gp

DEFAULT_WINDOW_DAYS = 90
"""Days of epoch per request. The request count depends on how many windows
there are, not on their width, so wide windows keep it low."""

MAX_ROWS_PER_WRITE = 400_000
"""Rows collected before they are written. Bounds memory by rows rather than
by days: at 2025 volumes a 90-day window is about 1.7 million element sets, and
holding a whole one ran a 16 GB machine out of memory mid-write."""


class ElementSource(Protocol):
    """What this module needs from Space-Track, and nothing more.

    Stated as a protocol so the fetch strategy can be tested against a stand-in
    without a network, a credential or a lake, and so the dependency is the
    two methods actually used rather than the whole client.
    """

    def gp_history(
        self, norad_ids: Sequence[int], start: dt.date, end: dt.date
    ) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class GpIngestReport:
    """Totals across every chunk of one ingestion run."""

    requests: int
    rows_written: int
    rows_quarantined: int
    partitions: int
    chunks_failed: int = 0
    empty_windows: tuple[str, ...] = ()

    @property
    def has_suspicious_gap(self) -> bool:
        """A window returned nothing while other windows returned plenty.

        Space-Track answers a throttled request with HTTP 200 and an empty JSON
        array -- not an error, not a 429. Without this the run reports a cheerful
        "0 rows" and moves on, leaving a hole nothing downstream can distinguish
        from a genuinely quiet quarter. That is exactly how a real backfill lost
        the last quarter of 2020 while reporting success.

        An empty window is only suspicious relative to a non-empty one. A
        backfill that starts before the first launch has legitimately empty
        windows, and those must not fail.
        """
        return bool(self.empty_windows) and self.rows_written > 0

    def describe(self) -> str:
        text = (
            f"gp_history: {self.rows_written:,} rows over {self.partitions} "
            f"partitions in {self.requests} chunk(s)"
        )
        if self.rows_quarantined:
            text += f", {self.rows_quarantined:,} quarantined"
        if self.chunks_failed:
            text += f", {self.chunks_failed} chunk(s) FAILED"
        if self.empty_windows:
            text += f", {len(self.empty_windows)} EMPTY window(s): {', '.join(self.empty_windows)}"
        return text


def windows(
    start: dt.date, end: dt.date, days: int = DEFAULT_WINDOW_DAYS
) -> Iterator[tuple[dt.date, dt.date]]:
    """Split ``[start, end)`` into consecutive half-open windows."""
    if days < 1:
        raise ValueError("window must be at least one day")
    cursor = start
    while cursor < end:
        stop = min(cursor + dt.timedelta(days=days), end)
        yield cursor, stop
        cursor = stop


def ingest_gp(
    settings: Settings,
    start: dt.date,
    end: dt.date,
    norad_ids: Sequence[int],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    max_rows_per_write: int = MAX_ROWS_PER_WRITE,
    checkpoint: Path | None = None,
    client: ElementSource | None = None,
    on_progress: Callable[[str], None] | None = None,
    continue_on_error: bool = True,
) -> GpIngestReport:
    """Land elements for ``norad_ids`` over ``[start, end)``.

    A failing chunk does not abandon the run by default: a backfill spanning
    hours should not lose everything to one bad response. Failures are counted
    and reported, and the chunk can simply be re-run.

    ``checkpoint`` names a file recording the windows this run has finished.
    A retry given the same file skips them instead of requesting them again --
    which matters because every request counts against Space-Track's limit.
    """
    owned = client is None
    spacetrack = client or SpaceTrackClient(settings.spacetrack)
    batch_size = settings.spacetrack.norad_ids_per_request
    done = _finished_windows(checkpoint)

    requests = written = quarantined = failed = 0
    seen_partitions: set[str] = set()
    empty_windows: list[str] = []

    def flush(collected: list[pl.DataFrame]) -> int:
        nonlocal written, quarantined
        # Written in chunks of about MAX_ROWS_PER_WRITE, not one write per
        # batch: a write costs time per *partition touched*, and every batch
        # touches every day of the window. See docs/phases/phase-1.md.
        combined = pl.concat(collected).sort(["norad_id", "epoch", "gp_id"])
        outcome = land(combined, GP_SPEC, settings, schema=gp.schema, source=gp.SOURCE)
        written += outcome.rows_written
        quarantined += outcome.rows_quarantined
        seen_partitions.update(outcome.partitions)
        return outcome.rows_written

    try:
        for window_start, window_end in windows(start, end, window_days):
            label = f"{window_start}..{window_end}"
            if label in done:
                if on_progress:
                    on_progress(f"  {label} -> landed by an earlier attempt of this run; skipped")
                continue

            collected: list[pl.DataFrame] = []
            pending = fetched = landed = window_failures = 0

            for batch in batched(list(norad_ids), batch_size):
                requests += 1
                try:
                    rows = spacetrack.gp_history(batch, window_start, window_end)
                except Exception as error:
                    failed += 1
                    window_failures += 1
                    if on_progress:
                        on_progress(
                            f"  FAILED {label} x{len(batch)}: {type(error).__name__}: {error}"
                        )
                    if not continue_on_error:
                        raise
                    continue
                frame = gp.to_frame(rows)
                fetched += frame.height
                if frame.height:
                    collected.append(frame)
                    pending += frame.height
                if pending >= max_rows_per_write:
                    landed += flush(collected)
                    collected, pending = [], 0

            if collected:
                landed += flush(collected)

            # Every request in the window succeeded and returned nothing.
            # Space-Track answers a throttled request with HTTP 200 and an
            # empty array, so this is recorded rather than shrugged at.
            if fetched == 0:
                empty_windows.append(label)
                if on_progress:
                    on_progress(f"  {label} -> EMPTY: no rows returned")
                continue

            if on_progress:
                on_progress(f"  {label} -> {landed:,} rows")
            if window_failures == 0:
                _record_finished(checkpoint, label)
    finally:
        if owned:
            spacetrack.close()

    return GpIngestReport(
        requests=requests,
        rows_written=written,
        rows_quarantined=quarantined,
        partitions=len(seen_partitions),
        chunks_failed=failed,
        empty_windows=tuple(empty_windows),
    )


def _finished_windows(checkpoint: Path | None) -> set[str]:
    if checkpoint is None or not checkpoint.exists():
        return set()
    return set(json.loads(checkpoint.read_text(encoding="utf-8")))


def _record_finished(checkpoint: Path | None, label: str) -> None:
    """Add a window to the checkpoint, atomically: a crash mid-write must not
    leave a file that makes the next attempt skip a window it never landed."""
    if checkpoint is None:
        return
    finished = sorted(_finished_windows(checkpoint) | {label})
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    temporary = checkpoint.with_suffix(".tmp")
    temporary.write_text(json.dumps(finished), encoding="utf-8")
    temporary.replace(checkpoint)
