Workflow
   │
   ▼
execute Activity:
recurring-watch.classify-update
   │
   ▼
Worker runs JevClassifier.classify()
   │
   ▼
relevant?
   │ yes
   ▼
LlmAgent(
    model=RetryingCadenceModel("gemini-3.5-flash-lite")
)
   │
   ▼
CadenceAgentRunner
   │
   ▼
GoogleADKActivities.generate_content_async
   │
   ▼
Gemini API