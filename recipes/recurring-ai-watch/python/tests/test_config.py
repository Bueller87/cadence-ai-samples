from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from config import CatalogError, load_selection


VALID_CATALOGS = {
    "agents.yaml": """agents:
  - id: google-adk
    framework: google-adk
""",
    "models.yaml": """models:
  - id: gemini-flash-lite
    provider: google
    model: gemini-3.5-flash-lite
    endpoint: https://generativelanguage.googleapis.com/v1
""",
    "classifiers.yaml": """classifiers:
  - id: jev-default
    provider: typesafe
    model: jev-latest
    endpoint: https://api.typesafe.ai/v1/systemone
""",
}


class CatalogLoaderTests(unittest.TestCase):
    def test_selects_entries_and_preserves_endpoints(self) -> None:
        with _CatalogDirectory() as directory:
            selection = load_selection(
                directory,
                agent_id="google-adk",
                model_id="gemini-flash-lite",
                classifier_id="jev-default",
            )

        self.assertEqual(selection.agent.framework, "google-adk")
        self.assertEqual(
            selection.model.endpoint, "https://generativelanguage.googleapis.com/v1"
        )
        self.assertEqual(
            selection.classifier.endpoint, "https://api.typesafe.ai/v1/systemone"
        )

    def test_rejects_unknown_id(self) -> None:
        with _CatalogDirectory() as directory:
            with self.assertRaisesRegex(CatalogError, "unknown models ID 'missing'"):
                load_selection(
                    directory,
                    agent_id="google-adk",
                    model_id="missing",
                    classifier_id="jev-default",
                )

    def test_rejects_duplicate_id(self) -> None:
        duplicate_agents = """agents:
  - id: google-adk
    framework: google-adk
  - id: google-adk
    framework: another-framework
"""
        with _CatalogDirectory({"agents.yaml": duplicate_agents}) as directory:
            with self.assertRaisesRegex(CatalogError, "duplicate agents ID 'google-adk'"):
                load_selection(
                    directory,
                    agent_id="google-adk",
                    model_id="gemini-flash-lite",
                    classifier_id="jev-default",
                )

    def test_rejects_missing_required_field(self) -> None:
        missing_endpoint = """models:
  - id: gemini-flash-lite
    provider: google
    model: gemini-3.5-flash-lite
"""
        with _CatalogDirectory({"models.yaml": missing_endpoint}) as directory:
            with self.assertRaisesRegex(CatalogError, "required non-empty string field 'endpoint'"):
                load_selection(
                    directory,
                    agent_id="google-adk",
                    model_id="gemini-flash-lite",
                    classifier_id="jev-default",
                )


class _CatalogDirectory:
    def __init__(self, replacements: dict[str, str] | None = None) -> None:
        self._replacements = replacements or {}
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        self._temporary_directory = tempfile.TemporaryDirectory()
        directory = Path(self._temporary_directory.name)
        for filename, contents in VALID_CATALOGS.items():
            (directory / filename).write_text(
                self._replacements.get(filename, contents), encoding="utf-8"
            )
        return directory

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        assert self._temporary_directory is not None
        self._temporary_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
