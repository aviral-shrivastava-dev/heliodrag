"""Jonathan McDowell's General Catalog of Artificial Space Objects (GCAT).

The only public catalogue carrying a per-satellite spacecraft-bus field together
with mass and span, which is what makes hardware generations knowable at all.

    McDowell, J. C., "General Catalog of Artificial Space Objects",
    https://planet4589.org/space/gcat

Licensed CC-BY. Unlike Space-Track, derived products from GCAT may be
redistributed with attribution, which is why the generation seed is committed.

The file is ~19 MB and regenerated daily, so it is cached on disk: a re-run is
then reproducible and does not repeatedly hammer a personal server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import httpx
import polars as pl

GCAT_SATCAT_URL: Final = "https://planet4589.org/space/gcat/tsv/cat/satcat.tsv"
USER_AGENT: Final = "starlink-drag-atlas/0.1 (academic research)"

WANTED_COLUMNS: Final[tuple[str, ...]] = (
    "JCAT",
    "Satcat",
    "Launch_Tag",
    "Piece",
    "Type",
    "Name",
    "LDate",
    "DDate",
    "Status",
    "Bus",
    "Mass",
    "DryMass",
    "Span",
    "Perigee",
    "Apogee",
    "Inc",
)


class GcatError(RuntimeError):
    """GCAT could not be fetched or parsed."""


def fetch(cache_path: Path, *, refresh: bool = False, timeout: float = 300.0) -> Path:
    """Download GCAT's satcat.tsv to ``cache_path``, reusing any cached copy."""
    if cache_path.exists() and not refresh:
        return cache_path

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(GCAT_SATCAT_URL, headers={"User-Agent": USER_AGENT})
    if response.status_code != 200:
        raise GcatError(f"GCAT returned {response.status_code}")
    cache_path.write_bytes(response.content)
    return cache_path


def load(path: Path) -> pl.DataFrame:
    """Parse GCAT's TSV into a frame of strings.

    GCAT carries its header on a ``#``-prefixed first line and uses further
    ``#`` lines as separators, so the header is read by hand and the rest
    filtered. Everything is read as text; typing happens after selection,
    because several columns carry qualifiers such as a trailing ``?``.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        raise GcatError(f"{path} is empty")

    header = [name.strip() for name in lines[0].lstrip("#").split("\t")]
    body = [line for line in lines[1:] if line and not line.startswith("#")]

    frame = pl.read_csv(
        "\n".join(body).encode("utf-8"),
        separator="\t",
        has_header=False,
        new_columns=header,
        infer_schema_length=0,
        truncate_ragged_lines=True,
        quote_char=None,
    )
    missing = [c for c in WANTED_COLUMNS if c not in frame.columns]
    if missing:
        raise GcatError(f"GCAT is missing expected columns: {missing}")

    return frame.select(WANTED_COLUMNS).with_columns(pl.col(pl.Utf8).str.strip_chars())
