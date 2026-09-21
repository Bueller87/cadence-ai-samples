# MacBook Pro — Cadence Mock Batch Test

## Goal

Run the same 10-ticket, concurrency-10 mock test on macOS to compare it with the slow Windows results.

- Branch: `kevin/batch-wip`
- AI provider: mock
- Child Workflows: current diagnostic stubs
- No Jev API key or live AI calls
- Windows comparison: 32.9–128.6 seconds for 10 concurrent tickets

**Only run this personal project on the work Mac if company policy permits it. Do not copy personal API keys or employer data between machines.**

## 1. Get the code

Open Terminal:

```bash
git clone https://github.com/Bueller87/cadence-ai-samples.git
cd cadence-ai-samples
git switch --track origin/kevin/batch-wip
```

If the repository is already cloned:

```bash
cd cadence-ai-samples
git fetch origin
git switch kevin/batch-wip
git pull --ff-only
```

Verify the branch:

```bash
git branch --show-current
```

Expected: `kevin/batch-wip`

## 2. Start Cadence

Install and launch Docker Desktop for Mac if needed.

Locate the project's Compose file:

```bash
find . \( -name 'compose.yaml' -o -name 'compose.yml' -o -name 'docker-compose.yml' -o -name 'docker-compose.yaml' \)
```

Change into the directory containing your Cadence Compose file and run:

```bash
docker compose up -d
docker compose ps
```

If the Compose setup lives outside this repository, use that location instead.

Wait until Cadence and Cassandra have started. Confirm that Cadence Web is accessible at:

http://localhost:8088

**Fresh Cadence installation:** Follow the repository's setup instructions to register the `cadence-ai-samples` domain before running tickets.

## 3. Build the native Mac executable

Check that Go is installed:

```bash
go version
```

From the repository root:

```bash
cd recipes/ticket-routing/go
go build -o ticket-router .
```

The resulting `ticket-router` is a **macOS executable**. Do not copy or attempt to run the Windows `.exe` on the Mac.

## 4. Terminal 1 — Start ONE mock worker

From `recipes/ticket-routing/go`:

```bash
AI_PROVIDER=mock ./ticket-router -mode worker
```

Leave this terminal running.

## 5. Terminal 2 — Run the benchmark

Open another Terminal window and navigate to the same Go directory:

```bash
cd cadence-ai-samples/recipes/ticket-routing/go
```

Adjust the path if your Terminal starts in a different directory.

Run:

```bash
AI_PROVIDER=mock ./ticket-router -mode batch -count 10 -concurrency 10 -batch-sla 1s
```

Record:

- Completed and failed executions
- Peak in-flight executions
- Batch wall-clock duration
- Minimum, average, and maximum per-execution wait

The current Child Workflow stubs return immediately without running an SLA timer, so **zero `SLA_TIMEOUT` results is expected**.

## 6. Optional sequential comparison

If the concurrent batch is slow, run the same batch with concurrency 1:

```bash
AI_PROVIDER=mock ./ticket-router -mode batch -count 10 -concurrency 1 -batch-sla 1s
```

Windows reference: **22.845 seconds**.

## 7. Stop

Press `Ctrl+C` in the worker terminal when finished.

Do not run 1,000 tickets or enable Jev yet. First compare the Mac results with Windows.