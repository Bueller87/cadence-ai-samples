#!/usr/bin/env bash

set -euo pipefail

PHASE="${1:-}"
if [[ "$PHASE" != "phase-a" && "$PHASE" != "phase-b" ]]; then
  echo "usage: $0 phase-a|phase-b" >&2
  exit 2
fi

if [[ "${AI_PROVIDER:-mock}" != "mock" ]]; then
  echo "refusing to run: AI_PROVIDER must be unset or mock" >&2
  exit 1
fi
if [[ -n "${TYPESAFE_API_KEY+x}" ]]; then
  echo "refusing to run: TYPESAFE_API_KEY must be unset" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
GO_DIR="$REPO_ROOT/recipes/ticket-routing/go"
BINARY="$GO_DIR/ticket-router"
RESULTS_FILE="$SCRIPT_DIR/results.csv"
RAW_DIR="$REPO_ROOT/benchmark-results/cadence-batch/$PHASE"
TASK_LIST="ticket-routing-mock-perf"
CADENCE_ADDRESS="127.0.0.1:7933"
CADENCE_DOMAIN="cadence-ai-samples"
COUNT=100
COOLDOWN_SECONDS=30
MAX_LOAD_1M="${MAX_LOAD_1M:-12}"
RANDOM_SEED=20260921

mkdir -p "$RAW_DIR"

if [[ ! -x "$BINARY" ]]; then
  echo "missing executable: $BINARY" >&2
  exit 1
fi

if [[ ! -f "$RESULTS_FILE" ]]; then
  echo "phase,run_index,concurrency,rep,start_utc,end_utc,wall_clock_ms,completed,failed,unroutable,acknowledged,sla_timeout,peak_in_flight,wait_min_ms,wait_avg_ms,wait_max_ms,throughput_per_sec,dispatch_timeout_warnings,cadence_cpu_pct,cassandra_cpu_pct,load_avg_1m" > "$RESULTS_FILE"
fi

load_average_1m() {
  sysctl -n vm.loadavg | awk '{print $2}'
}

load_is_acceptable() {
  python3 - "$1" "$MAX_LOAD_1M" <<'PY'
import sys
raise SystemExit(0 if float(sys.argv[1]) <= float(sys.argv[2]) else 1)
PY
}

wait_for_quiet_host() {
  local waited=0
  local load
  while true; do
    load="$(load_average_1m)"
    if load_is_acceptable "$load"; then
      printf '%s' "$load"
      return
    fi
    if (( waited >= 300 )); then
      echo "host load remained above $MAX_LOAD_1M for 300 seconds" >&2
      exit 1
    fi
    echo "host load $load exceeds $MAX_LOAD_1M; waiting 10 seconds" >&2
    sleep 10
    waited=$((waited + 10))
  done
}

wait_for_worker() {
  local waited=0
  while ! cadence --address "$CADENCE_ADDRESS" --domain "$CADENCE_DOMAIN" \
    tasklist describe --tasklist "$TASK_LIST" --tasklisttype workflow 2>/dev/null |
    rg -q 'POLLER IDENTITY'; do
    if (( waited >= 60 )); then
      echo "mock worker did not register on $TASK_LIST within 60 seconds" >&2
      exit 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

already_recorded() {
  python3 - "$RESULTS_FILE" "$PHASE" "$1" "$2" <<'PY'
import csv
import sys

path, phase, concurrency, rep = sys.argv[1:]
with open(path, newline="", encoding="utf-8") as handle:
    for row in csv.DictReader(handle):
        if (
            row["phase"] == phase
            and row["concurrency"] == concurrency
            and row["rep"] == rep
        ):
            raise SystemExit(0)
raise SystemExit(1)
PY
}

run_batch() {
  local label="$1"
  local concurrency="$2"
  local output_file="$3"

  echo "running $label: count=$COUNT concurrency=$concurrency"
  (
    cd "$GO_DIR"
    env -u TYPESAFE_API_KEY AI_PROVIDER=mock "$BINARY" \
      -mode batch \
      -task-list "$TASK_LIST" \
      -count "$COUNT" \
      -concurrency "$concurrency" \
      -batch-sla 1s
  ) >"$output_file" 2>&1
}

record_run() {
  local run_index="$1"
  local concurrency="$2"
  local rep="$3"
  local start_utc end_utc load output_file log_file warnings
  local cadence_cpu cassandra_cpu

  if already_recorded "$concurrency" "$rep"; then
    echo "skipping already recorded $PHASE concurrency=$concurrency rep=$rep"
    return
  fi

  load="$(wait_for_quiet_host)"
  start_utc="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  output_file="$RAW_DIR/run-$(printf '%02d' "$run_index")-c${concurrency}-r${rep}.txt"
  log_file="$RAW_DIR/run-$(printf '%02d' "$run_index")-c${concurrency}-r${rep}-cadence.log"

  run_batch "$PHASE run $run_index/12 rep=$rep" "$concurrency" "$output_file"
  end_utc="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  docker logs --since "$start_utc" --until "$end_utc" docker-cadence-1 >"$log_file" 2>&1 || true
  warnings="$(rg -c '"msg":"Async task dispatch timed out"' "$log_file" 2>/dev/null || true)"
  warnings="${warnings:-0}"
  cadence_cpu="$(docker stats --no-stream --format '{{.CPUPerc}}' docker-cadence-1 | tr -d '%')"
  cassandra_cpu="$(docker stats --no-stream --format '{{.CPUPerc}}' docker-cassandra-1 | tr -d '%')"

  RUN_PHASE="$PHASE" \
  RUN_INDEX="$run_index" \
  RUN_CONCURRENCY="$concurrency" \
  RUN_REP="$rep" \
  RUN_START_UTC="$start_utc" \
  RUN_END_UTC="$end_utc" \
  RUN_WARNINGS="$warnings" \
  RUN_CADENCE_CPU="$cadence_cpu" \
  RUN_CASSANDRA_CPU="$cassandra_cpu" \
  RUN_LOAD="$load" \
  python3 - "$output_file" "$RESULTS_FILE" <<'PY'
import csv
import os
import re
import sys

output_path, results_path = sys.argv[1:]
text = open(output_path, encoding="utf-8").read()

def value(label):
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise SystemExit(f"missing {label!r} in {output_path}")
    return match.group(1).strip()

def duration_ms(raw):
    units = {
        "h": 3_600_000,
        "m": 60_000,
        "s": 1_000,
        "ms": 1,
        "us": 0.001,
        "µs": 0.001,
        "ns": 0.000001,
    }
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|us|µs|ns|h|m|s)", raw)
    if not parts:
        raise SystemExit(f"cannot parse duration {raw!r}")
    return round(sum(float(number) * units[unit] for number, unit in parts), 3)

wait_match = re.search(
    r"^Per-execution client wait: min=(\S+) average=(\S+) max=(\S+)$",
    text,
    re.MULTILINE,
)
if not wait_match:
    raise SystemExit(f"missing client wait metrics in {output_path}")

row = {
    "phase": os.environ["RUN_PHASE"],
    "run_index": os.environ["RUN_INDEX"],
    "concurrency": os.environ["RUN_CONCURRENCY"],
    "rep": os.environ["RUN_REP"],
    "start_utc": os.environ["RUN_START_UTC"],
    "end_utc": os.environ["RUN_END_UTC"],
    "wall_clock_ms": duration_ms(value("Batch wall-clock duration")),
    "completed": value("Completed executions"),
    "failed": value("Failed executions"),
    "unroutable": value("UNROUTABLE"),
    "acknowledged": value("ACKNOWLEDGED"),
    "sla_timeout": value("SLA_TIMEOUT"),
    "peak_in_flight": value("Peak in-flight executions"),
    "wait_min_ms": duration_ms(wait_match.group(1)),
    "wait_avg_ms": duration_ms(wait_match.group(2)),
    "wait_max_ms": duration_ms(wait_match.group(3)),
    "throughput_per_sec": value("Average completed workflows per second"),
    "dispatch_timeout_warnings": os.environ["RUN_WARNINGS"],
    "cadence_cpu_pct": os.environ["RUN_CADENCE_CPU"],
    "cassandra_cpu_pct": os.environ["RUN_CASSANDRA_CPU"],
    "load_avg_1m": os.environ["RUN_LOAD"],
}

with open(results_path, "a", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=row.keys())
    writer.writerow(row)
PY

  cat "$output_file"
}

wait_for_worker

if [[ "${SKIP_WARMUPS:-0}" != "1" ]]; then
  for warmup in 1 2; do
    warmup_output="$RAW_DIR/warmup-${warmup}.txt"
    run_batch "$PHASE warmup $warmup/2 (discarded)" 10 "$warmup_output"
  done
fi
if [[ "${WARMUPS_ONLY:-0}" == "1" ]]; then
  exit 0
fi

run_index=0
while read -r concurrency rep; do
  run_index=$((run_index + 1))
  record_run "$run_index" "$concurrency" "$rep"
  if (( run_index < 12 )); then
    sleep "$COOLDOWN_SECONDS"
  fi
done < <(
  python3 - "$RANDOM_SEED" <<'PY'
import random
import sys

items = [(concurrency, rep) for concurrency in (1, 5, 10, 25) for rep in (1, 2, 3)]
random.Random(int(sys.argv[1])).shuffle(items)
for concurrency, rep in items:
    print(concurrency, rep)
PY
)
