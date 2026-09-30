#Worker Startup

**Cmd Line Args** (run from `python/google-adk/`)
--model-id gemini-flash-lite
--classifier-id jev-default
        │
        ▼
load_selection()
        │
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
        ├── create SystemOneClassifier(endpoint, model)
        └── create ADKActivities()
        │
        ▼
build_registry()
        │
        ├── recurring-watch.classify-update → SystemOneClassifier.classify
        └── GoogleADKActivities.generate_content_async → ADKActivities
