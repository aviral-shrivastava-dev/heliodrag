"""Generate the data dictionary from dbt's manifest.

Written rather than hand-maintained, because a hand-maintained dictionary is
wrong within a week and nobody notices. The descriptions live in the dbt schema
files next to the models they describe; this renders them, together with the
column types dbt observed in the warehouse, into one document.

Run via ``starlink-drag data-dictionary``, which ``make docs`` calls after
``dbt docs generate``.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

LAYER_ORDER = ("staging", "intermediate", "marts")
LAYER_TITLES = {
    "staging": "Staging (silver) — one-to-one with bronze",
    "intermediate": "Intermediate (silver) — business logic",
    "marts": "Marts (gold) — what everything else reads",
}


def _load(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def _layer(node: dict[str, Any]) -> str:
    parts = node.get("fqn", [])
    for candidate in LAYER_ORDER:
        if candidate in parts:
            return candidate
    return "other"


def _column_types(catalog: dict[str, Any], unique_id: str) -> dict[str, str]:
    node = catalog.get("nodes", {}).get(unique_id, {})
    return {name: column.get("type", "") for name, column in node.get("columns", {}).items()}


def _test_count(manifest: dict[str, Any]) -> dict[str, int]:
    """Map model unique_id -> how many tests are attached to it."""
    counts: dict[str, int] = {}
    for node in manifest["nodes"].values():
        if node["resource_type"] != "test":
            continue
        for parent in node["depends_on"]["nodes"]:
            counts[parent] = counts.get(parent, 0) + 1
    return counts


def render(manifest_path: Path, catalog_path: Path) -> str:
    """Render the dictionary as Markdown."""
    manifest = _load(manifest_path)
    catalog = _load(catalog_path) if catalog_path.exists() else {"nodes": {}}
    test_counts = _test_count(manifest)

    models = [node for node in manifest["nodes"].values() if node["resource_type"] == "model"]
    sources = list(manifest.get("sources", {}).values())

    lines: list[str] = [
        "# Data dictionary",
        "",
        "**Generated** — do not edit by hand. Descriptions live in the `schema.yml`",
        "files beside each model; run `starlink-drag data-dictionary` to rebuild",
        "this from dbt's manifest after `dbt docs generate`.",
        "",
        f"Last generated {dt.date.today().isoformat()} from "
        f"{len(models)} models and {len(sources)} sources.",
        "",
        "Medallion layers are dbt **tags**, not folders: staging and intermediate",
        "are `silver`, marts are `gold`, and bronze is the Iceberg landing zone",
        "outside dbt entirely.",
        "",
        "## Bronze sources",
        "",
        "Not tables in the warehouse: DuckDB views over each Iceberg table's",
        "current snapshot, rebuilt by `starlink-drag warehouse sync`. Bronze is",
        "append-only, so a re-run leaves duplicates for the intermediate layer to",
        "collapse — see [ADR-0005](adr/0005-bronze-appends-rather-than-replaces.md).",
        "",
    ]

    for source in sorted(sources, key=lambda s: str(s["name"])):
        lines.append(f"### `bronze.{source['name']}`")
        lines.append("")
        if source.get("description"):
            lines.append(_clean(source["description"]))
            lines.append("")
        columns = source.get("columns", {})
        if columns:
            lines.extend(_column_table(columns, {}))
            lines.append("")

    for layer in LAYER_ORDER:
        in_layer = sorted((m for m in models if _layer(m) == layer), key=lambda m: str(m["name"]))
        if not in_layer:
            continue
        lines.append(f"## {LAYER_TITLES[layer]}")
        lines.append("")
        for model in in_layer:
            lines.append(f"### `{model['name']}`")
            lines.append("")
            materialisation = model["config"].get("materialized", "view")
            tags = ", ".join(model["config"].get("tags", [])) or "—"
            tested = test_counts.get(model["unique_id"], 0)
            lines.append(f"*{materialisation}* · tags: {tags} · {tested} tests")
            lines.append("")
            if model.get("description"):
                lines.append(_clean(model["description"]))
                lines.append("")
            lines.extend(
                _column_table(model.get("columns", {}), _column_types(catalog, model["unique_id"]))
            )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _column_table(columns: dict[str, Any], types: dict[str, str]) -> list[str]:
    if not columns:
        return ["_No column-level documentation._"]
    rows = ["| Column | Type | Description |", "| --- | --- | --- |"]
    for name, column in columns.items():
        description = _clean(column.get("description", "")).replace("\n", " ")
        rows.append(f"| `{name}` | {types.get(name, '')} | {description} |")
    return rows


def _clean(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def write(manifest_path: Path, catalog_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(manifest_path, catalog_path), encoding="utf-8", newline="\n")
    return destination
