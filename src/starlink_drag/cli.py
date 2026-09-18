"""Command-line entry point.

Every pipeline operation is reachable here as well as through Dagster, so that
a plain cron or any other scheduler can drive the same code paths. Dagster
assets call into the same functions the CLI does; neither wraps the other.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Annotated

import typer

from starlink_drag import __version__
from starlink_drag.config import Settings, get_settings

app = typer.Typer(
    name="starlink-drag",
    help="Starlink Differential Drag Atlas pipeline.",
    no_args_is_help=True,
    add_completion=False,
)
ingest_app = typer.Typer(name="ingest", help="Land raw sources into bronze.", no_args_is_help=True)
app.add_typer(ingest_app)

StartOpt = Annotated[
    dt.datetime,
    typer.Option(formats=["%Y-%m-%d"], help="First day, inclusive (YYYY-MM-DD)."),
]
EndOpt = Annotated[
    dt.datetime,
    typer.Option(formats=["%Y-%m-%d"], help="Last day, exclusive (YYYY-MM-DD)."),
]
WindowOpt = Annotated[int, typer.Option(help="Days of epoch fetched per Space-Track request.")]
LimitOpt = Annotated[
    int,
    typer.Option(help="Use only the first N NORAD IDs. 0 means all. For smoke tests."),
]


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command("config")
def show_config() -> None:
    """Print resolved configuration, with secrets redacted.

    Use this first when something behaves differently between your machine and
    CI -- it is almost always an environment variable.
    """
    settings = get_settings()
    typer.echo(json.dumps(json.loads(settings.model_dump_json()), indent=2, default=str))


@app.command()
def doctor() -> None:
    """Report whether credentials are present, without revealing them."""
    settings = get_settings()

    checks = {
        "space-track credentials": settings.spacetrack.is_configured,
        "hapi endpoint": bool(settings.hapi.base_url),
        "data directory": settings.data_dir.exists(),
    }
    for label, ok in checks.items():
        typer.echo(f"{'ok  ' if ok else 'MISS'}  {label}")

    if not all(checks.values()):
        raise typer.Exit(code=1)


# -- ingestion --------------------------------------------------------------


@ingest_app.command("satcat")
def ingest_satcat_command() -> None:
    """Land the current Starlink catalogue as a dated snapshot."""
    from starlink_drag.ingest.satcat import ingest_satcat

    settings = get_settings()
    _require_credentials(settings)
    outcome = ingest_satcat(settings)
    typer.echo(outcome.describe())


@ingest_app.command("omni")
def ingest_omni_command(start: StartOpt, end: EndOpt) -> None:
    """Land NASA OMNI space weather over a date range."""
    from starlink_drag.ingest.omni import ingest_omni

    settings = get_settings()
    outcome = ingest_omni(settings, start.date(), end.date())
    typer.echo(outcome.describe())


@ingest_app.command("gp")
def ingest_gp_command(
    start: StartOpt,
    end: EndOpt,
    window_days: WindowOpt = 90,
    limit_objects: LimitOpt = 0,
) -> None:
    """Land Space-Track elements over a date range.

    Requires a SATCAT snapshot in bronze -- run `ingest satcat` first, since the
    object list comes from it.
    """
    from starlink_drag.ingest.gp import ingest_gp
    from starlink_drag.ingest.satcat import norad_ids

    settings = get_settings()
    _require_credentials(settings)

    ids = norad_ids(settings, on_orbit_during=(start.date(), end.date()))
    if limit_objects:
        ids = ids[:limit_objects]
    typer.echo(f"{len(ids):,} objects on orbit during the window")

    report = ingest_gp(
        settings,
        start.date(),
        end.date(),
        ids,
        window_days=window_days,
        on_progress=lambda line: typer.echo(line),
    )
    typer.echo(report.describe())
    if report.chunks_failed:
        raise typer.Exit(code=1)


@ingest_app.command("backfill")
def backfill_command(start: StartOpt, end: EndOpt, window_days: WindowOpt = 90) -> None:
    """Land SATCAT, OMNI and elements for a range, in that order."""
    from starlink_drag.ingest.gp import ingest_gp
    from starlink_drag.ingest.omni import ingest_omni
    from starlink_drag.ingest.satcat import ingest_satcat, norad_ids

    settings = get_settings()
    _require_credentials(settings)

    typer.echo("[1/3] satcat")
    typer.echo("  " + ingest_satcat(settings).describe())

    typer.echo("[2/3] omni")
    typer.echo("  " + ingest_omni(settings, start.date(), end.date()).describe())

    typer.echo("[3/3] gp_history")
    ids = norad_ids(settings, on_orbit_during=(start.date(), end.date()))
    typer.echo(f"  {len(ids):,} objects on orbit during the window")
    report = ingest_gp(
        settings,
        start.date(),
        end.date(),
        ids,
        window_days=window_days,
        on_progress=lambda line: typer.echo(line),
    )
    typer.echo("  " + report.describe())
    if report.chunks_failed:
        raise typer.Exit(code=1)


def _require_credentials(settings: Settings) -> None:
    if not settings.spacetrack.is_configured:
        typer.echo(
            "Space-Track credentials are absent. Set SPACETRACK_IDENTITY and "
            "SPACETRACK_PASSWORD in .env; see .env.example.",
            err=True,
        )
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
