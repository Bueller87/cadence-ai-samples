"""Portable loader for the three root YAML catalogs."""

from dataclasses import dataclass
from pathlib import Path

import yaml


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    id: str
    framework: str


@dataclass(frozen=True)
class ModelConfig:
    id: str
    provider: str
    model: str
    endpoint: str


@dataclass(frozen=True)
class ClassifierConfig:
    id: str
    provider: str
    model: str
    endpoint: str


@dataclass(frozen=True)
class CatalogSelection:
    agent: AgentConfig
    model: ModelConfig
    classifier: ClassifierConfig


def load_selection(
    catalog_dir: str | Path,
    *,
    agent_id: str,
    model_id: str,
    classifier_id: str,
) -> CatalogSelection:
    directory = Path(catalog_dir)
    agent = _select(directory / "agents.yaml", "agents", ("id", "framework"), agent_id)
    model = _select(
        directory / "models.yaml",
        "models",
        ("id", "provider", "model", "endpoint"),
        model_id,
    )
    classifier = _select(
        directory / "classifiers.yaml",
        "classifiers",
        ("id", "provider", "model", "endpoint"),
        classifier_id,
    )
    return CatalogSelection(
        agent=AgentConfig(**agent),
        model=ModelConfig(**model),
        classifier=ClassifierConfig(**classifier),
    )


def _select(
    path: Path, section: str, required: tuple[str, ...], selected_id: str
) -> dict[str, str]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CatalogError(f"catalog file not found: {path}") from error
    except yaml.YAMLError as error:
        raise CatalogError(f"invalid YAML in {path.name}: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get(section), list):
        raise CatalogError(f"{path.name}: expected a {section!r} list")

    seen: set[str] = set()
    selected: dict[str, str] | None = None
    for index, raw in enumerate(document[section]):
        if not isinstance(raw, dict):
            raise CatalogError(f"{path.name}: {section}[{index}] must be a mapping")
        entry = {field: raw.get(field) for field in required}
        missing = next(
            (field for field, value in entry.items() if not isinstance(value, str) or not value),
            None,
        )
        if missing:
            raise CatalogError(
                f"{path.name}: {section}[{index}] is missing required "
                f"non-empty string field {missing!r}"
            )
        if entry["id"] in seen:
            raise CatalogError(f"{path.name}: duplicate {section} ID {entry['id']!r}")
        seen.add(entry["id"])
        if entry["id"] == selected_id:
            selected = entry  # type: ignore[assignment]

    if selected is None:
        raise CatalogError(f"{path.name}: unknown {section} ID {selected_id!r}")
    return selected
