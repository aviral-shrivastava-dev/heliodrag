"""`src/` never imports `analysis/`: the pipeline must not depend on research
code. The dependency runs one way -- the analysis reads the gold marts and
borrows the explorer's palette -- and this holds it there."""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src"


def test_no_pipeline_module_imports_the_analysis() -> None:
    offenders = []
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [alias.name for alias in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            )  # fmt: skip
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] == "analysis"]

    assert offenders == []
