The folder picks the agent framework, and the CLI gives us two IDs: model and classifier. load_selection() looks those IDs up in the two root YAML catalogs and turns them into typed config objects.

When the Worker starts, we validate that combination, build the concrete System One classifier and Google ADK Activities, and register those implementations with the Cadence Worker.

The Workflow itself doesn’t know about API clients. It just schedules named Activities. Cadence routes those Activity calls to the implementations registered by the Worker. If Jev says the update matters, we build the ADK LlmAgent with the selected Gemini model, and the ADK integration turns the model call into a Cadence Activity.
