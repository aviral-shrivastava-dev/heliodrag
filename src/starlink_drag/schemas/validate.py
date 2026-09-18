"""Validation at the ingestion boundary, with quarantine instead of crashing.

A single malformed record in a batch of ninety thousand must not lose the other
eighty-nine thousand, and must not be silently dropped either. Rows that fail
their contract are written to a quarantine table carrying the reason, so a
backfill keeps going and the failures stay auditable.

Quarantine is not a dustbin. A non-empty quarantine is a signal: either the
source changed shape, or a contract is wrong. Phase 3 puts an asset check on it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaError, SchemaErrors

QUARANTINE_COLUMNS = ("source", "ingest_date", "failure_reason", "payload")


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """The result of validating one batch at the ingestion boundary."""

    valid: pl.DataFrame
    quarantined: pl.DataFrame
    reasons: dict[str, int] = field(default_factory=dict)

    @property
    def valid_count(self) -> int:
        return self.valid.height

    @property
    def quarantined_count(self) -> int:
        return self.quarantined.height

    @property
    def is_clean(self) -> bool:
        return self.quarantined.is_empty()


def validate(
    frame: pl.DataFrame,
    schema: pa.DataFrameSchema,
    *,
    source: str,
    ingest_date: dt.date | None = None,
) -> ValidationOutcome:
    """Split ``frame`` into rows that satisfy ``schema`` and rows that do not.

    Validation is lazy so that every failing row is collected in one pass rather
    than aborting at the first. Failures are keyed back to row positions, which
    is why the frame is given a stable row index first.
    """
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()

    if frame.is_empty():
        return ValidationOutcome(frame, _empty_quarantine())

    try:
        schema.validate(frame, lazy=True)
    except SchemaErrors as errors:
        failures = _failure_index(errors, frame.height)
    except SchemaError as error:  # a whole-frame failure, e.g. a missing column
        return ValidationOutcome(
            frame.clear(),
            _quarantine_rows(frame, source, ingest_date, str(error)),
            {str(error): frame.height},
        )
    else:
        return ValidationOutcome(frame, _empty_quarantine())

    if not failures:
        return ValidationOutcome(frame, _empty_quarantine())

    bad_positions = sorted(failures)
    mask = pl.Series("_bad", [i in failures for i in range(frame.height)])
    good = frame.filter(~mask)
    bad = frame.filter(mask)
    reasons = [failures[i] for i in bad_positions]

    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1

    return ValidationOutcome(good, _quarantine_rows(bad, source, ingest_date, reasons), counts)


def _failure_index(errors: SchemaErrors, height: int) -> dict[int, str]:
    """Map row position -> first failure reason for that row."""
    failures: dict[int, str] = {}
    cases = errors.failure_cases
    if not isinstance(cases, pl.DataFrame):
        cases = pl.from_pandas(cases)

    has_index = "index" in cases.columns
    for record in cases.iter_rows(named=True):
        reason = f"{record.get('column')}: {record.get('check')}"
        position = record.get("index") if has_index else None
        if position is None:
            # A check that cannot be attributed to a row condemns the batch.
            return dict.fromkeys(range(height), reason)
        index = int(position)
        failures.setdefault(index, reason)
    return failures


def _quarantine_rows(
    frame: pl.DataFrame,
    source: str,
    ingest_date: dt.date,
    reason: str | list[str],
) -> pl.DataFrame:
    """Serialise failing rows whole, so nothing is lost by quarantining them."""
    if frame.is_empty():
        return _empty_quarantine()

    payloads = [_as_json(row) for row in frame.iter_rows(named=True)]
    reasons = [reason] * frame.height if isinstance(reason, str) else reason
    return pl.DataFrame(
        {
            "source": [source] * frame.height,
            "ingest_date": [ingest_date] * frame.height,
            "failure_reason": reasons,
            "payload": payloads,
        },
        schema={
            "source": pl.Utf8,
            "ingest_date": pl.Date,
            "failure_reason": pl.Utf8,
            "payload": pl.Utf8,
        },
    )


def _as_json(row: dict[str, object]) -> str:
    import json

    return json.dumps(row, default=str, sort_keys=True)


def _empty_quarantine() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "source": pl.Utf8,
            "ingest_date": pl.Date,
            "failure_reason": pl.Utf8,
            "payload": pl.Utf8,
        }
    )
