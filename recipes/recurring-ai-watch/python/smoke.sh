#!/usr/bin/env bash
set -euo pipefail

# Start the matching Worker in another terminal before running this script.
# Override PYTHON, MODE, or TASK_LIST in the environment when needed.
PYTHON="${PYTHON:-python}"
MODE="${MODE:-mock}"
TASK_LIST="${TASK_LIST:-recurring-ai-watch-smoke}"
DOMAIN="cadence-ai-samples"
WORKFLOW_ID="watch-smoke-$(date +%s)-$$"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CLI="$SCRIPT_DIR/main.py"
COMMON=(--domain "$DOMAIN" --task-list "$TASK_LIST" --workflow-id "$WORKFLOW_ID")

if [[ "$MODE" != "mock" && "$MODE" != "live" ]]; then
    printf 'MODE must be mock or live\n' >&2
    exit 2
fi

trap 'printf "FAIL: %s. Inspect its Cadence history; it may still be running.\n" "$WORKFLOW_ID" >&2' ERR

run_watch() {
    "$PYTHON" "$CLI" "${COMMON[@]}" "$@"
}

read_status() {
    local text
    text="$(run_watch status)"
    printf '%s\n' "$text" >&2
    STATE="$(printf '%s\n' "$text" | sed -n 's/^state: //p')"
    COUNT="$(printf '%s\n' "$text" | sed -n 's/^check count: //p')"
    REPORT="$(printf '%s\n' "$text" | sed -n 's/^latest report: //p')"
    [[ -n "$STATE" && "$COUNT" =~ ^[0-9]+$ && -n "$REPORT" ]]
}

wait_for_report() {
    local deadline=$((SECONDS + 120))
    while (( SECONDS < deadline )); do
        sleep 1
        read_status
        if [[ "$STATE" == "WAITING" && "$REPORT" != "none" ]]; then
            return
        fi
    done
    printf 'Timed out waiting for WAITING with a report\n' >&2
    return 1
}

wait_for_check() {
    local previous_count="$1"
    local deadline=$((SECONDS + 60))
    while (( SECONDS < deadline )); do
        sleep 1
        read_status
        if [[ "$STATE" == "WAITING" && "$COUNT" -gt "$previous_count" ]]; then
            [[ "$COUNT" -eq $((previous_count + 1)) ]]
            return
        fi
    done
    printf 'Timed out waiting for check-now\n' >&2
    return 1
}

wait_for_stop() {
    local expected_count="$1"
    local deadline=$((SECONDS + 30))
    while (( SECONDS < deadline )); do
        sleep 1
        read_status
        [[ "$COUNT" -eq "$expected_count" ]]
        if [[ "$STATE" == "STOPPED" ]]; then
            return
        fi
    done
    printf 'Workflow did not reach STOPPED\n' >&2
    return 1
}

run_watch start --mode "$MODE" --interval 15
wait_for_report

before="$COUNT"
run_watch check-now
wait_for_check "$before"

before_stop="$COUNT"
run_watch stop
wait_for_stop "$before_stop"

sleep 16
read_status
[[ "$STATE" == "STOPPED" && "$COUNT" -eq "$before_stop" ]]

trap - ERR
printf 'PASS: CLI smoke for %s. Inspect Cadence history for Activity ordering and terminal outcome.\n' "$WORKFLOW_ID"
