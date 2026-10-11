# Implementation prompt

Extend __RECIPE_TITLE__ without moving external calls into Workflow code.
Keep `classifiers.yaml` and `models.yaml` as the only live provider, model, and
endpoint sources. Keep every catalog selection and credential Worker-local and
out of Workflow input and history. Preserve the stable agent Activity,
catalog-free mock path, explicit `ALLOW_DUPLICATE` demo policy, and
duplicate-open rejection.
Do not import another recipe or template profile.
