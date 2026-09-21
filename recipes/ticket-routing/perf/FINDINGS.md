# Cadence local batch throughput findings

## Summary

The original throughput collapse did not reproduce under controlled conditions.
The stale Cadence image with debug logging completed the concurrency-25 runs at
30.42 to 32.80 workflows per second, compared with the earlier informal result of
1.43 workflows per second. None of the 24 measured runs emitted an async task
dispatch timeout.

Updating the Cadence server and changing its log level to `info` produced a
repeatable improvement at concurrency 25. Median throughput increased from
31.33 to 35.22 workflows per second, or 12.4%, while median average client wait
fell from 741 ms to 639 ms, or 13.8%. The lower concurrency levels were
effectively unchanged.

Recommendation: keep the latest server image and `info`
logging. The evidence does not support attributing the earlier 1.43
workflows-per-second incident to the stale image and debug logging alone. That
incident was more likely a transient workflow-task dispatch failure.

## Experiment

- Date: 2026-09-21
- Host: Apple Silicon Mac with 16 logical CPUs and 64 GiB host memory
- Docker Desktop allocation: 16 CPUs and 7.75 GiB memory
- Cadence domain: `cadence-ai-samples`
- Worker count: one
- Worker task list: `ticket-routing-mock-perf`
- Provider: deterministic mock only
- Batch size: 100 workflows
- Concurrency levels: 1, 5, 10, and 25
- Runs: three per concurrency and phase
- Warmup: two discarded 100-workflow runs per phase
- Pause between runs: 30 seconds
- Randomization seed: `20260921`

Phase A used Cadence server image
`sha256:e8b5b09bc344af3dbb020715ef36074656524dca8601c08da2f2c8c535856852`
with `LOG_LEVEL=debug`.

Phase B used Cadence server image
`sha256:5927f3f8523c4ebb1833cc602fe4958a06453835410390ad0d49a1825dad5f9d`
with `LOG_LEVEL=info`.

## Results

### Concurrency 1

- Phase A median throughput: 7.98 workflows/s, range 7.73 to 8.12
- Phase B median throughput: 7.58 workflows/s, range 7.16 to 7.91
- Median change: -5.0%
- Phase A median average wait: 125 ms
- Phase B median average wait: 132 ms

### Concurrency 5

- Phase A median throughput: 13.01 workflows/s, range 13.01 to 13.24
- Phase B median throughput: 13.04 workflows/s, range 12.96 to 13.31
- Median change: +0.2%
- Phase A median average wait: 376 ms
- Phase B median average wait: 371 ms

### Concurrency 10

- Phase A median throughput: 18.60 workflows/s, range 18.25 to 18.89
- Phase B median throughput: 18.79 workflows/s, range 18.58 to 19.08
- Median change: +1.0%
- Phase A median average wait: 514 ms
- Phase B median average wait: 507 ms

### Concurrency 25

- Phase A median throughput: 31.33 workflows/s, range 30.42 to 32.80
- Phase B median throughput: 35.22 workflows/s, range 34.67 to 36.06
- Median change: +12.4%
- Phase A median average wait: 741 ms
- Phase B median average wait: 639 ms

All three Phase B concurrency-25 observations exceeded all three Phase A
observations. This improvement was repeatable. The experiment does not separate
the image update from the logging change.

## Hypothesis assessment

### H1: throughput rises and then reaches a saturation point

Throughput rose monotonically through concurrency 25 in both phases. The
experiment did not locate a point where throughput stopped rising. Individual workflow latency
increased as concurrency rose, which is expected when more workflows share the
same local server and worker.

The estimated average number of workflows in flight, calculated as throughput
times average wait, was close to the configured concurrency:

- Concurrency 1: approximately 1.0 average workflow in flight
- Concurrency 5: approximately 4.9
- Concurrency 10: approximately 9.4 to 9.6
- Concurrency 25: approximately 22.8 to 22.9

The difference at concurrency 25 comes from the start and end of the batch. The
batch executor kept the downstream system supplied with work and was not the
observed bottleneck.

### H2: the stale image and debug logging caused the original collapse

The results do not support this hypothesis. Phase A retained the stale image and
debug logging but did not reproduce the collapse. Its median concurrency-25 throughput was 21.9
times the earlier informal result.

The image and logging changes improved concurrency-25 throughput by 12.4%.
They cannot explain a drop from roughly 31 to 1.43
workflows per second.

### Dispatch timeout mechanism

There were zero `Async task dispatch timed out` warnings in all 24 measured
runs, so warning-to-latency correlation could not be calculated. The earlier
incident had 20 such warnings and a Child Workflow delayed by 61 seconds. That
contrast supports treating the earlier result as a transient dispatch incident.

## Logging and environment observations

- Phase A raw Cadence logs consumed approximately 958 MiB.
- Phase B raw Cadence logs consumed approximately 1.1 MiB.
- Changing to `info` reduced captured log volume by roughly 870 times.
- All 2,400 measured workflows completed with zero execution failures.
- No TypeSafe Jev API key was present and no live AI endpoint was contacted.
- CPU samples were snapshots taken after each run. They do not measure
  continuous utilization, so they are not used for causal conclusions.
- The adjusted throughput drift was small: approximately -0.05 workflows/s per
  run in Phase A and -0.07 workflows/s per run in Phase B.

## Recommendation

Keep the environment in its current Phase B state:

- latest pulled `ubercadence/server:master-auto-setup` image
- `LOG_LEVEL=info`
- Docker Desktop memory unchanged until a separate memory experiment
- Cassandra heap unchanged until a separate persistence experiment

Do not run the full 12-run follow-up matrix now. The original collapse did
not reproduce, and the operational choice is already clear. If exact
attribution of the 12.4% concurrency-25 gain becomes important, run a narrower
three-replicate concurrency-25 comparison using the latest image with debug
logging. That isolates logging at one-fifth of the cost of another full matrix.

Do not use these numbers as production capacity claims. The experiment used one
laptop, one worker, a small Docker allocation, deterministic mock
classification, and diagnostic Child Workflow stubs that return immediately.
It did not exercise service-level agreement timers, acknowledgments, provider latency, or real
business workflow work.
