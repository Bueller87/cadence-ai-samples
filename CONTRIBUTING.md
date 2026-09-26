# Contributing

Start a contribution with a specific problem or use case, not a library feature.

Before implementation:

1. Describe the proposed use case and why durable workflow orchestration helps.
2. Run `./scripts/new-recipe.sh your-recipe-name` to create the Python starter,
   then tailor its `SPEC.md`.
3. Define how the recipe can be exercised with mock or synthetic data without paid AI API access.

When implementing a recipe:

- Keep it independently runnable and free of dependencies on other recipes.
- Keep workflow and Activity code easy to copy into another project.
- Include a README that explains how to run the recipe, what it does, and what users should expect.
- Include mock or synthetic test data that contains no real customer information or proprietary data.
- Keep credentials, secrets, and environment-specific configuration out of the repository.
- Do not use employer-owned code, internal documentation, proprietary configurations, credentials, or other non-public assets.
- Avoid unnecessary abstractions, shared integration layers, and premature refactoring.

Modularization is optional. A workflow, its Activities, and related business logic may remain together in one file when that is the clearest presentation.
