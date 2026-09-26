"""Commands for the optional streaming path (Phase 6).

Registered on the main ``starlink-drag`` app in ``cli.py``. The Kafka client is
imported inside each command, not here: it is in the ``stream`` dependency
group, which the batch image does not install, and the rest of the CLI must
keep working without it.
"""

from __future__ import annotations

from typing import Annotated

import typer

stream_app = typer.Typer(
    name="stream",
    help="Streaming drag nowcast: Redpanda, a producer and a consumer. Optional.",
    no_args_is_help=True,
)

OnceOpt = Annotated[bool, typer.Option(help="Run one cycle and exit, instead of forever.")]


@stream_app.command("topics")
def topics_command() -> None:
    """Create the stream's topics if they are missing."""
    from starlink_drag.config import get_settings
    from starlink_drag.stream import topics

    created = topics.ensure(get_settings().stream)
    typer.echo(f"created: {', '.join(created)}" if created else "all topics already exist")


@stream_app.command("produce")
def produce_command(once: OnceOpt = False) -> None:
    """Publish Starlink element sets (hourly) and SWPC space weather (every few minutes)."""
    from starlink_drag.config import get_settings
    from starlink_drag.stream import producer

    producer.run(get_settings(), once=once, report=typer.echo)


@stream_app.command("consume")
def consume_command(
    stop_when_idle: Annotated[
        float | None,
        typer.Option(help="Exit after this many seconds with no new messages. For testing."),
    ] = None,
) -> None:
    """Keep the per-generation drag nowcast up to date in its own Iceberg table."""
    from starlink_drag.config import get_settings
    from starlink_drag.stream import consumer

    written = consumer.run(get_settings(), report=typer.echo, stop_when_idle=stop_when_idle)
    typer.echo(f"wrote {written} snapshot(s)")
