# Dagster webserver and daemon, running this project.
#
# uv rather than pip, and the lockfile copied before the source, so a source
# change does not reinstall 186 packages.

FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    DAGSTER_HOME=/opt/dagster/home

WORKDIR /opt/atlas

# Dependency layer: changes only when the lockfile does.
# LICENSE is required at build time, not merely shipped: pyproject.toml
# declares `license = { file = "LICENSE" }`, and hatchling fails the project
# build without it.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --group serve

# Project layer.
COPY src/ ./src/
COPY transform/ ./transform/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group serve

RUN mkdir -p "$DAGSTER_HOME" && printf 'telemetry:\n  enabled: false\n' > "$DAGSTER_HOME/dagster.yaml"

ENV PATH="/opt/atlas/.venv/bin:$PATH"

EXPOSE 3000

CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "3000", "-m", "starlink_drag.definitions"]
