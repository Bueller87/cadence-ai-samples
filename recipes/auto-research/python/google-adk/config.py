"""Load one model selection from the repository catalog."""

from dataclasses import dataclass
from pathlib import Path

import yaml


class CatalogError(ValueError):
    """The model catalog or requested selection is invalid."""


@dataclass(frozen=True)
class ModelConfig:
    id: str
    provider: str
    model: str
    endpoint: str


def load_model(catalog_dir: str | Path, model_id: str) -> ModelConfig:
    path = Path(catalog_dir) / "models.yaml"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CatalogError(f"catalog file not found: {path}") from error
    except yaml.YAMLError as error:
        raise CatalogError(f"invalid YAML in models.yaml: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("models"), list):
        raise CatalogError("models.yaml: expected a 'models' list")

    selected: dict[str, str] | None = None
    seen: set[str] = set()
    required = ("id", "provider", "model", "endpoint")
    for index, raw in enumerate(document["models"]):
        if not isinstance(raw, dict):
            raise CatalogError(f"models.yaml: models[{index}] must be a mapping")
        entry = {field: raw.get(field) for field in required}
        missing = next(
            (
                field
                for field, value in entry.items()
                if not isinstance(value, str) or not value
            ),
            None,
        )
        if missing:
            raise CatalogError(
                "models.yaml: "
                f"models[{index}] is missing required non-empty string field {missing!r}"
            )
        if entry["id"] in seen:
            raise CatalogError(f"models.yaml: duplicate models ID {entry['id']!r}")
        seen.add(entry["id"])
        if entry["id"] == model_id:
            selected = entry  # type: ignore[assignment]

    if selected is None:
        raise CatalogError(f"models.yaml: unknown models ID {model_id!r}")
    return ModelConfig(**selected)
