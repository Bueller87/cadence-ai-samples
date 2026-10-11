# __RECIPE_TITLE__ customization prompt

Use the following prompt with a coding assistant after generation:

---

Adapt this generated `python/google-adk` Cadence recipe to its documented business
scenario in `SPEC.md`.

Preserve these constraints:

1. Keep mock mode fully offline and deterministic. Require an explicit
   `--mode mock|live` on both `worker` and `start`.
2. Keep model, agent, network, filesystem, clock, and other nondeterministic work
   inside Cadence Activities.
3. Keep live catalog selection, provider clients, and `MODEL_AI_KEY` in the
   Worker process. Start must remain configuration-free. Do not put model,
   provider, credential, or endpoint configuration in Workflow input or history.
4. Have only the live Worker read model selections from `models.yaml`; do not
   add classifier configuration or read `classifiers.yaml`.
5. Reject unsupported model IDs, provider mismatches, and unsafe endpoints before
   the live Worker polls.
6. Require `--confirm-live` before a live Worker reads catalogs or credentials
   or builds provider clients. Do not expose this option on `start`.
7. Preserve the stable demo Workflow ID, `ALLOW_DUPLICATE` reuse policy, and the
   clear duplicate-open-run error.
8. Keep literal argparse `worker` and `start` commands compatible with the Local
   Samples Explorer.
9. Do not import source from another recipe or create a shared recipe runtime.
10. Update `README.md` and `SPEC.md`, then add offline tests for Workflow,
    Activities, configuration, command parsing, live validation, and failures.

Use only synthetic test data. Do not add real customer information, credentials,
provider calls in core tests, or unresolved template markers.

Return a concise summary of changed files, exact validation commands and results,
untested behavior, and known limitations.
