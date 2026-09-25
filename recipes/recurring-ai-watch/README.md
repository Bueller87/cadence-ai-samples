# Recurring AI Watch

A fictional application's background jobs depend on a library with three scripted
release notes. Cadence checks immediately, then every 15 seconds:

`scripted update → Jev: relevant? → optional Gemini impact report → durable wait → repeat`

Mock mode is the default and needs no credentials. The live path supports only
Google ADK + Gemini and TypeSafe Jev. There is no real release polling or agent tool use.

## Setup

Use Python 3.12 or newer and a running Cadence server at `localhost:7833`, with
the existing domain `cadence-ai-samples`. Run from the repository root:

```powershell
Set-Location recipes/recurring-ai-watch/python
py -m venv .venv
$Python = (Resolve-Path '.venv/Scripts/python.exe').Path
& $Python -m pip install -e .
```

The sample pins Cadence Python SDK 0.4.0 and Google ADK 2.1.0. For the existing
development checkout, the already-tested interpreter can instead be selected with:

```powershell
$Python = (Resolve-Path '../../../spikes/cadence-python-agent-compat/.venv/Scripts/python.exe').Path
```

## Mock run

Terminal 1 (leave the Worker running):

```powershell
& $Python .\main.py --task-list recurring-ai-watch-mock worker
```

Terminal 2 (same directory and interpreter selection):

```powershell
& $Python .\main.py --task-list recurring-ai-watch-mock start
& $Python .\main.py status
& $Python .\main.py check-now
& $Python .\main.py stop
```

The default Workflow ID is `recurring-ai-watch-demo`. Supply `--workflow-id`
before the command to operate on another Watch. `start --interval 15` sets the
interval in seconds. `start` submits the Workflow and returns immediately.

## Live run

In Terminal 1, set `MODEL_AI_KEY` to a valid Google Gemini API key and
`CLASSIFIER_AI_KEY` to a valid TypeSafe Jev key using your local credential setup.
These variables belong only in the Worker session. Do not put their values in
commands saved to the repository, catalog files, or Workflow inputs.

```powershell
& $Python .\main.py --domain cadence-ai-samples `
    --task-list recurring-ai-watch-live `
    --agent-id google-adk --model-id gemini-flash-lite --classifier-id jev-default `
    worker --mode live --confirm-live
```

Terminal 2:

```powershell
& $Python .\main.py --task-list recurring-ai-watch-live start --mode live
& $Python .\main.py status
& $Python .\main.py check-now
& $Python .\main.py stop
```

Worker/start resolve IDs from the root YAML catalogs; `--catalog-dir` supports a
copied sample. Endpoints remain Worker-local. Only the official native Google
endpoint is supported. Other catalog combinations and substituted Google gateway
endpoints are rejected. Keep mock and live Workers on separate task lists.

For an automated CLI smoke, keep the matching Worker running and execute this
**as a script file** in Terminal 2:

```powershell
& .\smoke.ps1 -Python $Python -Mode live -TaskList recurring-ai-watch-live
# Or, with the mock Worker:
& .\smoke.ps1 -Python $Python -Mode mock -TaskList recurring-ai-watch-mock
```

It uses a unique Workflow ID, waits for a report, sends check-now, stops while
waiting, and checks the count stays fixed. Command errors and assertion failures
exit with code 1 before PASS. On failure, inspect the printed Workflow ID in
Cadence Web; if still open, stop it with `--workflow-id <id> stop`.

In Cadence Web, select `cadence-ai-samples` and inspect that Workflow's history.
Expect `recurring-watch.classify-update` first and
`GoogleADKActivities.generate_content_async` only after a relevant decision.
The status Query is `watch-status`. Its state is application state: after a fatal
Workflow failure it may still show the last `CHECKING` state. Use the execution's
terminal status/history to determine success or failure.

## Durability and failures

- One cycle runs at a time. A stop during a check finishes that check; a stop
  during waiting completes without another check.
- AI Activities have at most three attempts with exponential backoff. An
  exhausted transient failure leaves the release position unchanged. The next
  cycle repeats Jev, even if Gemini was the operation that failed.
- Authentication, configuration, and schema errors fail the Watch. Google can
  report an invalid key as HTTP 400; this is treated as authentication failure.
- After all releases are consumed, checks retain the latest report without more
  AI calls. Continue-As-New every 20 checks carries essential state and waits
  the normal interval before the next check. Each run has a 365-day timeout.
- A generated report is advice to review, not verified knowledge about the
  fictional application. Retried provider calls may incur additional usage.

## Validation

```powershell
& $Python -m unittest discover -s tests -v
& $Python -m py_compile config.py workflow.py live.py main.py
# Optional: existing local Cadence server; isolated mock Worker, no AI calls.
& $Python .\tests\local_cadence_checks.py
```

The released SDK's in-memory tests auto-fire timers, so they cannot faithfully
inject stop while timer-waiting or demonstrate server-side Activity retries.
The smoke and local-server checks cover those boundaries. Local checks exercise
Continue-As-New, exhausted-retry recovery, and stop during an active Activity.

Live verification on September 25, 2026 used Workflow
`watch-smoke-368f97966fab4e27bb88239bc6851b6d`: three Jev Activities, one completed
Gemini Activity, a retained report, timer/check-now wake-ups, and STOPPED at check
count 3 with no Activity after stop. This is a functional smoke, not a benchmark.
