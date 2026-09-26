"""Commands that move the lake between storage backends.

Registered on the main ``starlink-drag`` app in ``cli.py``.
"""

from __future__ import annotations

import typer


def lake_copy_command() -> None:
    """Copy the local lake into the configured S3 lake. No API calls.

    The target is whatever LAKE_BACKEND=r2 and the other LAKE_* settings point
    at -- the Docker stack's SeaweedFS, or Cloudflare R2. Safe to run again if
    it dies: chunks already copied are skipped. Every table's row count is
    checked against the source afterwards.
    Then build the warehouse from it wherever it will be used, e.g. inside the
    Docker stack.
    """
    from starlink_drag import lake
    from starlink_drag.config import get_settings
    from starlink_drag.lake_copy import copy_lake

    settings = get_settings()
    if not lake.is_remote(settings):
        typer.echo(
            "The configured lake is local, so there is nowhere to copy to. Set "
            "LAKE_BACKEND=r2 and LAKE_ENDPOINT_URL / LAKE_BUCKET / keys for the target.",
            err=True,
        )
        raise typer.Exit(code=1)

    source = settings.model_copy(
        update={"lake": settings.lake.model_copy(update={"backend": "local"})}
    )
    # dlt keeps per-pipeline state on disk, keyed by table name: a separate
    # working directory keeps the copy from touching the local pipeline's.
    target = settings.model_copy(update={"data_dir": settings.data_dir / "lake-copy"})

    typer.echo(f"Copying {lake.root(source)} -> {lake.root(target)}")
    try:
        copy_lake(source, target, report=typer.echo)
    except (ValueError, RuntimeError) as problem:
        typer.echo(str(problem), err=True)
        raise typer.Exit(code=1) from problem
    typer.echo("Copied and verified.")
