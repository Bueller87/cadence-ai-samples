import tempfile
import unittest
from pathlib import Path

from config import CatalogError, load_model


VALID_MODELS = """models:
  - id: gemini-flash-lite
    provider: google
    model: gemini-3.5-flash-lite
    endpoint: https://generativelanguage.googleapis.com
"""


class CatalogTests(unittest.TestCase):
    def test_loads_model_without_classifier_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "models.yaml").write_text(
                VALID_MODELS, encoding="utf-8"
            )
            model = load_model(directory, "gemini-flash-lite")
            self.assertFalse((directory / "classifiers.yaml").exists())
        self.assertEqual(model.provider, "google")

    def test_rejects_unknown_duplicate_and_incomplete_models(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            path = directory / "models.yaml"
            path.write_text(VALID_MODELS, encoding="utf-8")
            with self.assertRaisesRegex(CatalogError, "unknown models ID"):
                load_model(directory, "missing")

            path.write_text(
                VALID_MODELS
                + """  - id: gemini-flash-lite
    provider: openai
    model: gpt-test
    endpoint: https://api.openai.com/v1
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CatalogError, "duplicate models ID"):
                load_model(directory, "gemini-flash-lite")

            path.write_text(
                "models:\n  - id: incomplete\n    provider: openai\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CatalogError, "required non-empty"):
                load_model(directory, "incomplete")


if __name__ == "__main__":
    unittest.main()
