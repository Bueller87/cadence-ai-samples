"""Strict catalog loading for Worker-local inference configuration."""

from dataclasses import dataclass
from pathlib import Path

import yaml


class CatalogError(ValueError):
    """A catalog or requested selection is invalid."""


@dataclass(frozen=True)
class ClassifierConfig:
    id: str
    provider: str
    model: str
    endpoint: str


@dataclass(frozen=True)
class ModelConfig:
    id: str
    provider: str
    model: str
    endpoint: str


def _load(catalog_dir: str | Path, filename: str, collection: str, selected_id: str):
    path = Path(catalog_dir) / filename
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CatalogError(f"catalog file not found: {path}") from error
    except yaml.YAMLError as error:
        raise CatalogError(f"invalid YAML in {filename}: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get(collection), list):
        raise CatalogError(f"{filename}: expected a {collection!r} list")
    selected = None
    seen: set[str] = set()
    required = ("id", "provider", "model", "endpoint")
    for index, raw in enumerate(document[collection]):
        if not isinstance(raw, dict):
            raise CatalogError(f"{filename}: {collection}[{index}] must be a mapping")
        entry = {field: raw.get(field) for field in required}
        missing = next((field for field, value in entry.items()
                        if not isinstance(value, str) or not value.strip()), None)
        if missing:
            raise CatalogError(
                f"{filename}: {collection}[{index}] is missing required "
                f"non-empty string field {missing!r}"
            )
        if entry["id"] in seen:
            raise CatalogError(f"{filename}: duplicate {collection} ID {entry['id']!r}")
        seen.add(entry["id"])
        if entry["id"] == selected_id:
            selected = entry
    if selected is None:
        raise CatalogError(f"{filename}: unknown {collection} ID {selected_id!r}")
    return selected


def load_classifier(catalog_dir: str | Path, classifier_id: str) -> ClassifierConfig:
    return ClassifierConfig(**_load(
        catalog_dir, "classifiers.yaml", "classifiers", classifier_id
    ))


def load_model(catalog_dir: str | Path, model_id: str) -> ModelConfig:
    return ModelConfig(**_load(catalog_dir, "models.yaml", "models", model_id))
