#Worker Startup

**Cmd Line Args**
--agent-id google-adk
--model-id gemini-flash-lite
--classifier-id jev-default
        │
        ▼
load_selection()
        │
        ├── agents.yaml      → google-adk
        ├── models.yaml      → gemini-3.5-flash-lite + endpoint
        └── classifiers.yaml → jev-latest + endpoint
        │
        ▼
CatalogSelection
        │
        ▼
Worker startup
        │
        ├── validate supported combination
        ├── MODEL_AI_KEY → GOOGLE_API_KEY
        ├── create JevClassifier(endpoint, model)
        └── create GeminiActivities()
        │
        ▼
build_registry()
        │
        ├── recurring-watch.classify-update → JevClassifier.classify
        └── GoogleADKActivities.generate_content_async → GeminiActivities