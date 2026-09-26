"""Build the dbt docs as one static page that can be published.

``dbt docs generate`` needs a warehouse: the catalog -- every column's type --
is read from the database, not from the project files. The real warehouse is
Space-Track data that may not be redistributed, and CI has none anyway.

So the page is built against a warehouse created empty for the purpose: bronze
tables with the production schema and no rows, the models run with dbt's
``--empty`` flag, and the generation seed loaded because it is derived from
GCAT, which is CC-BY and public already. The published catalog carries the
real type of every column and no data at all -- not by policy, but because
there is none in the database it was built from.

``.github/workflows/docs.yml`` runs this and deploys the page to GitHub Pages.
"""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import duckdb
import polars as pl

from starlink_drag.dbt_runner import dbt, prepare
from starlink_drag.ingest.bronze import AUDIT_SCHEMA
from starlink_drag.schemas import gp, omni, satcat
from starlink_drag.schemas.validate import QUARANTINE_SCHEMA


def empty_bronze(database: Path) -> None:
    """Every table dbt reads as a source, with production types and no rows."""
    frames = {
        "gp_history": gp.to_frame([]),
        "satcat": satcat.to_frame([], dt.date.today()),
        "omni": omni.to_frame([]),
        "quarantine": pl.DataFrame(schema=QUARANTINE_SCHEMA),
        "ingest_audit": pl.DataFrame(schema=AUDIT_SCHEMA),
    }
    with duckdb.connect(str(database)) as connection:
        connection.execute("create schema if not exists bronze")
        for name, frame in frames.items():
            arrow = frame.to_arrow()  # noqa: F841 - read by name in the query below
            connection.execute(f"create table bronze.{name} as select * from arrow")


def build(output: Path, report: Callable[[str], None] = print) -> int:
    """Write ``output/index.html``: the dbt docs, lineage graph included."""
    code = prepare(report)
    if code != 0:
        return code

    with tempfile.TemporaryDirectory() as scratch:
        # Named like the real warehouse, so the page shows the same relation names.
        database = Path(scratch) / "atlas.duckdb"
        target = Path(scratch) / "target"
        empty_bronze(database)

        for step in (("seed",), ("run", "--empty"), ("docs", "generate", "--static")):
            report("dbt " + " ".join(step))
            code = dbt(*step, database=database, target=target)
            if code != 0:
                return code

        output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target / "static_index.html", output / "index.html")

    report(f"wrote {output / 'index.html'}")
    return 0
