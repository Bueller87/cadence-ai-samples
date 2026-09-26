import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestWorkflowEnvironment

from config import CatalogError, load_selection
from inference import runtime_model
from live import LiveConfigurationError, validate_live
from workflow import WORKFLOW, RecipeInput, RecipeResult, build_registry


ROOT = Path(__file__).resolve().parents[4]


class StarterTests(unittest.IsolatedAsyncioTestCase):
    def test_loads_catalog_selection_and_rejects_unknown_id(self) -> None:
        selection = load_selection(
            ROOT,
            agent_id="google-adk",
            model_id="gemini-flash-lite",
            classifier_id="jev-default",
        )
        self.assertEqual(selection.model.model, "gemini-3.5-flash-lite")
        with self.assertRaisesRegex(CatalogError, "unknown models ID"):
            load_selection(
                ROOT,
                agent_id="google-adk",
                model_id="missing",
                classifier_id="jev-default",
            )

    def test_maps_all_six_agent_model_paths(self) -> None:
        expected = {
            ("google-adk", "google"): "gemini",
            ("google-adk", "ollama"): "ollama_chat/model",
            ("google-adk", "openai"): "openai/model",
            ("openai-agents", "google"): "model",
            ("openai-agents", "ollama"): "model",
            ("openai-agents", "openai"): "model",
        }
        for (framework, provider), model in expected.items():
            source = "gemini" if (framework, provider) == ("google-adk", "google") else "model"
            self.assertEqual(runtime_model(framework, provider, source), model)

    def test_rejects_cataloged_but_unimplemented_classifier(self) -> None:
        selection = load_selection(
            ROOT,
            agent_id="google-adk",
            model_id="gemini-flash-lite",
            classifier_id="laya-local",
        )
        with self.assertRaisesRegex(LiveConfigurationError, "only jev-default"):
            validate_live(selection)

    async def test_mock_workflow_skips_or_runs_model(self) -> None:
        with TestWorkflowEnvironment(
            build_registry(), data_converter=PydanticDataConverter()
        ) as environment:
            for suffix, text, expected in (
                ("skip", "Routine request.", None),
                ("run", "Investigate this request.", "Mock analysis: Investigate this request."),
            ):
                execution = await environment.client.start_workflow(
                    WORKFLOW,
                    RecipeInput(text),
                    workflow_id=f"starter-{suffix}",
                    task_list="test-task-list",
                )
                result = environment.get_workflow_result(
                    RecipeResult, execution.workflow_id, execution.run_id
                )
                self.assertEqual(result.output, expected)


if __name__ == "__main__":
    unittest.main()
