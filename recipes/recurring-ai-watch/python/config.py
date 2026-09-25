"""Portable loader for the Recurring AI Watch YAML catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class CatalogError(ValueError):
    """Raised when a catalog is missing, malformed, or has no selected entry."""


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
    """Load catalogs from *catalog_dir* and select entries by their exact IDs."""

    directory = Path(catalog_dir)
    agents = _load_entries(directory / "agents.yaml", "agents", ("id", "framework"))
    models = _load_entries(
        directory / "models.yaml", "models", ("id", "provider", "model", "endpoint")
    )
    classifiers = _load_entries(
        directory / "classifiers.yaml",
        "classifiers",
        ("id", "provider", "model", "endpoint"),
    )

    agent = _select(agents, agent_id, "agents.yaml", "agents")
    model = _select(models, model_id, "models.yaml", "models")
    classifier = _select(classifiers, classifier_id, "classifiers.yaml", "classifiers")

    return CatalogSelection(
        agent=AgentConfig(id=agent["id"], framework=agent["framework"]),
        model=ModelConfig(
            id=model["id"],
            provider=model["provider"],
            model=model["model"],
            endpoint=model["endpoint"],
        ),
        classifier=ClassifierConfig(
            id=classifier["id"],
            provider=classifier["provider"],
            model=classifier["model"],
            endpoint=classifier["endpoint"],
        ),
    )


def _load_entries(
    path: Path, collection_name: str, required_fields: tuple[str, ...]
) -> list[dict[str, str]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"catalog file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise CatalogError(f"invalid YAML in {path.name}: {exc}") from exc

    if not isinstance(document, dict) or not isinstance(document.get(collection_name), list):
        raise CatalogError(f"{path.name}: expected a {collection_name!r} list")

    seen_ids: set[str] = set()
    entries: list[dict[str, str]] = []
    for index, raw_entry in enumerate(document[collection_name]):
        if not isinstance(raw_entry, dict):
            raise CatalogError(f"{path.name}: {collection_name}[{index}] must be a mapping")

        entry: dict[str, str] = {}
        for field in required_fields:
            value = raw_entry.get(field)
            if not isinstance(value, str) or not value:
                raise CatalogError(
                    f"{path.name}: {collection_name}[{index}] is missing required "
                    f"non-empty string field {field!r}"
                )
            entry[field] = value

        if entry["id"] in seen_ids:
            raise CatalogError(
                f"{path.name}: duplicate {collection_name} ID {entry['id']!r}"
            )
        seen_ids.add(entry["id"])
        entries.append(entry)

    return entries


def _select(
    entries: list[dict[str, str]], entry_id: str, filename: str, collection_name: str
) -> dict[str, str]:
    for entry in entries:
        if entry["id"] == entry_id:
            return entry
    raise CatalogError(f"{filename}: unknown {collection_name} ID {entry_id!r}")
