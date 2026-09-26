"""Commands that serve or publish what the pipeline built.

Registered on the main ``starlink-drag`` app in ``cli.py``, which covers the
pipeline itself. They live apart so neither file outgrows a quick read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

PortOpt = Annotated[int, typer.Option(help="Port for the explorer.")]


def demo_command(
    days: Annotated[
        int, typer.Option(min=1, help="How many recent days to ingest. 30 takes minutes.")
    ] = 30,
    port: PortOpt = 8501,
) -> None:
    """From a fresh clone to a running explorer: ingest recent days, model, serve.

    Needs a free Space-Track account in .env; it says how if one is missing.
    For the full 2020-to-present history, run `starlink-drag backfill` instead.
    """
    from starlink_drag.launch import demo

    code = demo(days=days, port=port, report=typer.echo)
    if code != 0:
        raise typer.Exit(code=code)


def app_command(port: PortOpt = 8501) -> None:
    """Start the decay explorer on the warehouse you already have."""
    from starlink_drag.launch import serve

    code = serve(port=port, report=typer.echo)
    if code != 0:
        raise typer.Exit(code=code)


def docs_site_command(
    output: Annotated[Path, typer.Option(help="Directory to write index.html into.")] = Path(
        "site"
    ),
) -> None:
    """Build the dbt docs as one static page, against an empty warehouse.

    The page carries every model, column type and the lineage graph, and no
    data: it is built from tables created empty, so it can be published.
    """
    from starlink_drag.docs_site import build

    code = build(output, report=typer.echo)
    if code != 0:
        raise typer.Exit(code=code)


def data_dictionary_command() -> None:
    """Regenerate docs/data_dictionary.md from dbt's manifest.

    Generated rather than hand-written: a hand-maintained dictionary is wrong
    within a week and nobody notices. Run `make docs` (dbt docs generate) first.
    """
    from starlink_drag.data_dictionary import write

    transform = Path("transform") / "target"
    manifest = transform / "manifest.json"
    if not manifest.exists():
        typer.echo(f"{manifest} not found. Run `make docs` (dbt docs generate) first.", err=True)
        raise typer.Exit(code=1)

    written = write(manifest, transform / "catalog.json", Path("docs") / "data_dictionary.md")
    typer.echo(f"wrote {written}")
