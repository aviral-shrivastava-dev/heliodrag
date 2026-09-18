"""Dagster resources. Thin wiring only.

Configuration is read by ``starlink_drag.config``, not here; this exposes it to
assets and points the dbt resource at the project.
"""

from __future__ import annotations

from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, DbtProject

from starlink_drag.config import Settings, get_settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DBT_PROJECT_DIR = REPOSITORY_ROOT / "transform"


class AtlasSettings(dg.ConfigurableResource):  # type: ignore[misc]
    """Hands assets the process-wide settings object.

    A resource rather than a direct import so that a test can substitute one,
    and so the Dagster UI shows what the run was configured with.
    """

    def load(self) -> Settings:
        return get_settings()


dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR, profiles_dir=DBT_PROJECT_DIR)
dbt_project.prepare_if_dev()

dbt_resource = DbtCliResource(project_dir=dbt_project)


def resources() -> dict[str, object]:
    return {"settings": AtlasSettings(), "dbt": dbt_resource}
