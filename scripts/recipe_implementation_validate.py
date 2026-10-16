#!/usr/bin/env python3
"""Validate a staged recipe implementation tree before install; stdlib only."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ARTIFACT_SEGMENTS = (
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".run-state",
    ".gradle",
    "build",
    ".DS_Store",
)
ARTIFACT_SUFFIXES = (".egg-info",)

GO_RECIPE_IMPORT = re.compile(
    r'"github\.com/cadence-workflow/cadence-ai-samples/recipes/([a-z][a-z0-9-]*)'
)
RECIPES_REF = re.compile(r"recipes/([a-z][a-z0-9-]+)")


def _relative_path_is_artifact(rel: str) -> bool:
    parts = rel.split("/") if rel else []
    if any(part in ARTIFACT_SEGMENTS for part in parts):
        return True
    return any(part.endswith(ARTIFACT_SUFFIXES) for part in parts)


def artifact_hits(root: Path) -> list[str]:
    hits: list[str] = []
    candidates = {root, *root.rglob("*")}
    for path in sorted(candidates):
        if path == root:
            continue
        rel = path.relative_to(root).as_posix()
        if _relative_path_is_artifact(rel):
            hits.append(rel)
    return hits


def symlink_hits(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_symlink()
    )


def cross_recipe_hits(root: Path, slug: str) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".py":
            hits.extend(_python_hits(path, text, slug, rel))
        elif path.suffix == ".go" or path.name == "go.mod":
            hits.extend(_go_hits(text, slug, rel))
        elif path.suffix == ".java":
            hits.extend(_java_hits(text, slug, rel))
        else:
            hits.extend(_string_hits(text, slug, rel))
    return hits


def _string_hits(text: str, slug: str, rel: str) -> list[str]:
    out: list[str] = []
    for match in RECIPES_REF.finditer(text.replace(f"recipes/{slug}", "")):
        out.append(f"{rel}: recipes/{match.group(1)}")
    return out


def _python_hits(path: Path, text: str, slug: str, rel: str) -> list[str]:
    out: list[str] = []
    slug_token = slug.replace("-", "_")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                out.append(f"{rel}: relative import from {node.module or ''}")
                continue
            if node.module:
                modules = [node.module]
        for module in modules:
            if not module:
                continue
            if module.startswith("recipes.") and not module.startswith(f"recipes.{slug_token}"):
                out.append(f"{rel}: import {module}")
            for match in RECIPES_REF.finditer(module):
                if match.group(1) != slug:
                    out.append(f"{rel}: import path recipes/{match.group(1)}")
    return out


def _go_hits(text: str, slug: str, rel: str) -> list[str]:
    out: list[str] = []
    for match in GO_RECIPE_IMPORT.finditer(text):
        if match.group(1) != slug:
            out.append(f"{rel}: import recipes/{match.group(1)}")
    return out


def _java_hits(text: str, slug: str, rel: str) -> list[str]:
    out: list[str] = []
    slug_token = slug.replace("-", "")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("import "):
            continue
        if "recipes/" in stripped:
            for match in RECIPES_REF.finditer(stripped):
                if match.group(1) != slug:
                    out.append(f"{rel}: import recipes/{match.group(1)}")
        pkg = stripped[len("import ") :].rstrip(";").strip()
        if pkg.startswith("recipes.") and not pkg.startswith(f"recipes.{slug_token}"):
            out.append(f"{rel}: import {pkg}")
    return out


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: recipe_implementation_validate.py <tree-root> <recipe-slug> source|staged",
            file=sys.stderr,
        )
        return 2
    tree_root = Path(sys.argv[1]).resolve()
    slug = sys.argv[2]
    phase = sys.argv[3]
    if phase not in {"source", "staged"}:
        print("phase must be source or staged", file=sys.stderr)
        return 2
    artifact_label = "profile source" if phase == "source" else "staged implementation"
    symlink_label = artifact_label
    artifacts = artifact_hits(tree_root)
    if artifacts:
        print(f"generated artifact in {artifact_label}: {artifacts[0]}", file=sys.stderr)
        return 1
    symlinks = symlink_hits(tree_root)
    if symlinks:
        print(f"symlink in {symlink_label}: {symlinks[0]}", file=sys.stderr)
        return 1
    launcher = tree_root / "run.sh"
    if not launcher.is_file() or not launcher.stat().st_mode & 0o111:
        print(
            f"missing executable run.sh in {artifact_label}",
            file=sys.stderr,
        )
        return 1
    cross = cross_recipe_hits(tree_root, slug)
    if cross:
        print(f"cross-recipe import reference: {cross[0]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
