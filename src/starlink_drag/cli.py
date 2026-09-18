"""Command-line entry point.

Every pipeline operation is reachable here as well as through Dagster, so that
a plain cron or any other scheduler can drive the same code paths. Dagster
assets call into the same functions the CLI does; neither wraps the other.
"""

from __future__ import annotations

import json

import typer

from starlink_drag import __version__
from starlink_drag.config import get_settings

app = typer.Typer(
    name="starlink-drag",
    help="Starlink Differential Drag Atlas pipeline.",
    no_args_is_help=True,
    add_completion=False,
)


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


if __name__ == "__main__":
    app()
