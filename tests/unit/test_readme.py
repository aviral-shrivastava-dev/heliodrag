"""The README is the acceptance criterion for Phase 5, so it is tested.

"Someone who has never seen the repository can run it using only the README"
fails quietly: a renamed file, a stale diagram or a command that no longer
exists does not break any build. These checks make those failures loud.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from starlink_drag.cli import app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
README = REPOSITORY_ROOT / "README.md"
DIAGRAM = REPOSITORY_ROOT / "docs" / "architecture.mmd"

MERMAID = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _mermaid(document: Path) -> str:
    blocks = MERMAID.findall(document.read_text(encoding="utf-8"))
    assert len(blocks) == 1, f"{document.name} should embed exactly one diagram"
    return str(blocks[0]).strip()


@pytest.mark.parametrize("document", ["README.md", "docs/architecture.md"])
def test_the_embedded_diagram_is_the_committed_one(document: str) -> None:
    assert _mermaid(REPOSITORY_ROOT / document) == DIAGRAM.read_text(encoding="utf-8").strip()


def _headings(markdown: Path) -> set[str]:
    anchors = set()
    for line in markdown.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            text = line.lstrip("#").strip().lower()
            text = re.sub(r"[^\w\- ]", "", text)
            anchors.add(text.replace(" ", "-"))
    return anchors


def test_every_relative_link_resolves() -> None:
    broken = []
    for target in LINK.findall(README.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        resolved = REPOSITORY_ROOT / path
        missing_file = not resolved.exists()
        missing_heading = (
            not missing_file
            and bool(anchor)
            and resolved.suffix == ".md"
            and anchor not in _headings(resolved)
        )
        if missing_file or missing_heading:
            broken.append(target)
    assert not broken, f"README links to missing files or headings: {broken}"


def test_every_phase_document_that_exists_is_listed() -> None:
    text = README.read_text(encoding="utf-8")
    written = sorted(p.name for p in (REPOSITORY_ROOT / "docs" / "phases").glob("phase-*.md"))

    assert written, "no phase documents found"
    missing = [name for name in written if f"docs/phases/{name}" not in text]
    assert not missing, f"README does not link {missing}"


def test_the_one_command_is_real() -> None:
    text = README.read_text(encoding="utf-8")
    assert "uv run starlink-drag demo" in text

    result = CliRunner().invoke(app, ["demo", "--help"])
    assert result.exit_code == 0
