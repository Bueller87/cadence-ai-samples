"""Fault fixture checks, kept separate from attendee exercise tests."""
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cadence.contrib.pydantic import PydanticDataConverter
from cadence.testing import TestWorkflowEnvironment

import main
from activities import SEARCH_ACTIVITY, search_invalid_sources, search_research_sources
from workflow import WORKFLOW, RecipeInput, RecipeResult, build_registry


class FaultFlagTests(unittest.IsolatedAsyncioTestCase):
    def test_mock_flag_and_registry_selection(self):
        args = main.parser().parse_args(['worker', '--mode', 'mock', '--fault', 'invalid-source'])
        self.assertEqual(args.fault, 'invalid-source')
        self.assertIs(build_registry(search_activity=search_invalid_sources).get_activity(SEARCH_ACTIVITY), search_invalid_sources)
        self.assertIs(build_registry().get_activity(SEARCH_ACTIVITY), search_research_sources)
        self.assertIsNone(main.parser().parse_args(['worker', '--mode', 'mock']).fault)
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            main.parser().parse_args(['worker', '--mode', 'mock', '--fault', 'unknown'])

    async def test_live_fault_is_rejected_before_loading_or_connecting(self):
        with (patch('main.load_model') as load, patch('main.client') as client,
              patch('sys.stderr', new_callable=io.StringIO) as stderr,
              self.assertRaises(SystemExit) as error):
            await main.main(['worker', '--mode', 'live', '--confirm-live', '--fault', 'invalid-source'])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('requires --mode mock', stderr.getvalue())
        load.assert_not_called()
        client.assert_not_called()

    async def test_worker_selects_fault_registry_and_announces_it(self):
        event = MagicMock()
        event.wait = AsyncMock()
        with (patch('main.build_registry', wraps=build_registry) as registry,
              patch('main.client'), patch('main.Worker'),
              patch('main.asyncio.Event', return_value=event),
              patch('builtins.print') as output):
            await main.main(['worker', '--mode', 'mock', '--fault', 'invalid-source'])
        registry.assert_called_once_with(search_activity=search_invalid_sources)
        self.assertIn('fault=invalid-source', output.call_args.args[0])

    async def test_faulty_workflow_fails_at_real_source_reader(self):
        with TestWorkflowEnvironment(build_registry(search_activity=search_invalid_sources),
                                     data_converter=PydanticDataConverter()) as environment:
            execution = await environment.client.start_workflow(
                WORKFLOW, RecipeInput('Durable orchestration', 'mock'),
                workflow_id='fault-fixture', task_list='fault-test')
            with self.assertRaisesRegex(ValueError, 'synthetic research.example'):
                environment.get_workflow_result(RecipeResult, execution.workflow_id, execution.run_id)


if __name__ == '__main__':
    unittest.main()
