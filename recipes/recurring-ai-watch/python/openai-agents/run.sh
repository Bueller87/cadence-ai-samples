#!/bin/sh
set -eu
umask 077

if [ "${CADENCE_RECIPE_LAUNCHER_BOUND:-0}" != 1 ]; then
  ORIGIN_PATH=${CADENCE_RECIPE_LAUNCHER_PATH:-$0}
  ORIGIN_DIR=$(CDPATH= cd -- "$(dirname -- "$ORIGIN_PATH")" && pwd -P)
  BIND_STATE="$ORIGIN_DIR/.run-state"
  mkdir -p -- "$BIND_STATE"
  [ ! -L "$BIND_STATE" ] && [ -d "$BIND_STATE" ] ||
    { echo "Unsafe launcher state path." >&2; exit 1; }
  chmod 700 "$BIND_STATE"
  for stale_snapshot in "$BIND_STATE"/launcher.snapshot.*; do
    [ -e "$stale_snapshot" ] || continue
    stale_pid=${stale_snapshot##*.}
    case "$stale_pid" in ''|*[!0-9]*) continue;; esac
    if ! kill -0 "$stale_pid" 2>/dev/null && [ ! -L "$stale_snapshot" ] &&
        [ -f "$stale_snapshot" ]; then
      rm -f -- "$stale_snapshot"
    fi
  done
  SNAPSHOT="$BIND_STATE/launcher.snapshot.$$"
  rm -f -- "$SNAPSHOT"
  (set -C; : >"$SNAPSHOT") 2>/dev/null ||
    { echo "Could not bind the launcher safely." >&2; exit 1; }
  trap 'rm -f -- "$SNAPSHOT"' EXIT HUP INT TERM
  cp -- "$ORIGIN_PATH" "$SNAPSHOT"
  chmod 700 "$SNAPSHOT"
  CADENCE_RECIPE_LAUNCHER_PATH=$ORIGIN_PATH
  CADENCE_RECIPE_LAUNCHER_CKSUM=$(cksum "$SNAPSHOT" | awk '{print $1 ":" $2}')
  CADENCE_RECIPE_LAUNCHER_BOUND=1
  CADENCE_RECIPE_LAUNCHER_SNAPSHOT=$SNAPSHOT
  export CADENCE_RECIPE_LAUNCHER_PATH CADENCE_RECIPE_LAUNCHER_CKSUM
  export CADENCE_RECIPE_LAUNCHER_BOUND CADENCE_RECIPE_LAUNCHER_SNAPSHOT
  exec /bin/sh "$SNAPSHOT" "$@"
fi

SCRIPT_PATH=${CADENCE_RECIPE_LAUNCHER_PATH:-$0}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)
STATE_DIR="$SCRIPT_DIR/.run-state"
READY="$STATE_DIR/ready.v1"
LOCK="$STATE_DIR/setup.lock"
OWNER="$STATE_DIR/setup.owner.$$"
RECOVERY="$STATE_DIR/setup.recovery.$$"
LEGACY_RECOVERY="$STATE_DIR/setup.recovery"
LOG="$STATE_DIR/setup.log.tmp"
MARKER="$STATE_DIR/ready.tmp.$$"
LOCK_HELD=0
RECOVERY_HELD=0

remove_snapshot() {
  snapshot=${CADENCE_RECIPE_LAUNCHER_SNAPSHOT:-}
  expected_snapshot="$STATE_DIR/launcher.snapshot.$$"
  if [ "$snapshot" = "$expected_snapshot" ] && [ ! -L "$snapshot" ] &&
      [ -f "$snapshot" ]; then
    rm -f -- "$snapshot"
  fi
}

clear_launcher_binding() {
  remove_snapshot
  unset CADENCE_RECIPE_LAUNCHER_PATH CADENCE_RECIPE_LAUNCHER_CKSUM
  unset CADENCE_RECIPE_LAUNCHER_BOUND CADENCE_RECIPE_LAUNCHER_SNAPSHOT
}

cleanup() {
  rm -f -- "$MARKER" "$LOG" "$OWNER"
  remove_snapshot
  if [ "$LOCK_HELD" -eq 1 ]; then rm -f -- "$LOCK"; fi
  if [ "$RECOVERY_HELD" -eq 1 ]; then rm -rf -- "$RECOVERY"; fi
}
trap cleanup EXIT HUP INT TERM

fingerprint() {
  cksum "$1" | awk '{print $1 ":" $2}'
}

EXECUTED_LAUNCHER_FINGERPRINT=${CADENCE_RECIPE_LAUNCHER_CKSUM:-$(fingerprint "$SCRIPT_DIR/run.sh")}
case "$EXECUTED_LAUNCHER_FINGERPRINT" in
  *[!0-9:]*|:*|*:) echo "Invalid launcher fingerprint." >&2; exit 1;;
esac

launcher_fingerprint() {
  current=$(fingerprint "$SCRIPT_DIR/run.sh")
  [ "$current" = "$EXECUTED_LAUNCHER_FINGERPRINT" ] ||
    { echo "Launcher changed during setup. Retry." >&2; return 1; }
  printf '%s\n' "$EXECUTED_LAUNCHER_FINGERPRINT"
}

expected_marker() {
  launcher=$(launcher_fingerprint) || return 1
  printf 'version=1\nlauncher=%s\ndependency=pyproject.toml:%s\n' \
    "$launcher" "$(fingerprint "$SCRIPT_DIR/pyproject.toml")"
}

valid_state_path() {
  [ ! -L "$STATE_DIR" ] && { [ ! -e "$STATE_DIR" ] || [ -d "$STATE_DIR" ]; } &&
    [ ! -L "$READY" ] && { [ ! -e "$READY" ] || [ -f "$READY" ]; }
}

marker_current() {
  valid_state_path || return 1
  [ -f "$READY" ] || return 1
  [ "$(wc -c <"$READY")" -le 4096 ] || return 1
  expected_marker | cmp -s - "$READY"
}

python_compatible() {
  "$1" - "$SCRIPT_DIR/pyproject.toml" "${2-}" <<'PY'
import sys
import tomllib
from pathlib import Path

with open(sys.argv[1], "rb") as stream:
    requirement = tomllib.load(stream).get("project", {}).get("requires-python")
if not isinstance(requirement, str) or not requirement:
    raise SystemExit(2)
if len(sys.argv) > 2 and sys.argv[2]:
    if Path(sys.prefix).resolve() != Path(sys.argv[2]).resolve():
        raise SystemExit(3)
try:
    from packaging.specifiers import SpecifierSet
except ImportError:
    from pip._vendor.packaging.specifiers import SpecifierSet
version = ".".join(str(part) for part in sys.version_info[:3])
raise SystemExit(0 if SpecifierSet(requirement).contains(version) else 1)
PY
}

select_python() {
  seen=
  for candidate in python3.13 python3.12 python3; do
    path=$(command -v "$candidate" 2>/dev/null || true)
    [ -n "$path" ] || continue
    case ":$seen:" in *":$path:"*) continue;; esac
    seen="${seen:+$seen:}$path"
    if python_compatible "$path"; then printf '%s\n' "$path"; return 0; fi
  done
  printf '%s\n' "No installed Python interpreter satisfies pyproject.toml requires-python." >&2
  return 1
}

failure_excerpt() {
  printf '%s\n' "Setup failed. Diagnostic excerpt:" >&2
  sed -E \
    -e 's#https?://[^[:space:]]+#[redacted-url]#g' \
    -e 's/([A-Za-z0-9_]*[Kk][Ee][Yy][A-Za-z0-9_]*[[:space:]]*=[[:space:]]*).*/\1[redacted]/g' \
    -e 's/([A-Za-z0-9_]*[Tt][Oo][Kk][Ee][Nn][A-Za-z0-9_]*[[:space:]]*=[[:space:]]*).*/\1[redacted]/g' \
    -e 's/([A-Za-z0-9_]*[Ss][Ee][Cc][Rr][Ee][Tt][A-Za-z0-9_]*[[:space:]]*=[[:space:]]*).*/\1[redacted]/g' \
    -e 's/([A-Za-z0-9_]*[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd][A-Za-z0-9_]*[[:space:]]*=[[:space:]]*).*/\1[redacted]/g' \
    -e 's/([Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn][[:space:]]*[:=][[:space:]]*).*/\1[redacted]/g' \
    -e 's/[Bb][Ee][Aa][Rr][Ee][Rr][[:space:]][^[:space:]]+/Bearer [redacted]/g' \
    "$LOG" | tail -c 4096 >&2
}

prepare_log() {
  rm -f -- "$LOG" || { echo "Could not clear the setup diagnostic log." >&2; return 1; }
  (set -C; : >"$LOG") 2>/dev/null ||
    { echo "Could not create the setup diagnostic log safely." >&2; return 1; }
  [ ! -L "$LOG" ] && [ -f "$LOG" ] &&
    chmod 600 "$LOG" || { echo "Unsafe setup diagnostic log." >&2; return 1; }
}

venv_path_safe() {
  [ ! -L "$SCRIPT_DIR/.venv" ] &&
    { [ ! -e "$SCRIPT_DIR/.venv" ] || [ -d "$SCRIPT_DIR/.venv" ]; } ||
    { echo "Unsafe Python virtual environment path." >&2; return 1; }
  if [ -d "$SCRIPT_DIR/.venv" ]; then
    [ ! -L "$SCRIPT_DIR/.venv/bin" ] &&
      { [ ! -e "$SCRIPT_DIR/.venv/bin" ] || [ -d "$SCRIPT_DIR/.venv/bin" ]; } ||
      { echo "Unsafe Python virtual environment contents." >&2; return 1; }
    [ ! -L "$SCRIPT_DIR/.venv/pyvenv.cfg" ] &&
      { [ ! -e "$SCRIPT_DIR/.venv/pyvenv.cfg" ] ||
        [ -f "$SCRIPT_DIR/.venv/pyvenv.cfg" ]; } ||
      { echo "Unsafe Python virtual environment contents." >&2; return 1; }
    [ ! -L "$SCRIPT_DIR/.venv/lib" ] &&
      { [ ! -e "$SCRIPT_DIR/.venv/lib" ] || [ -d "$SCRIPT_DIR/.venv/lib" ]; } ||
      { echo "Unsafe Python virtual environment contents." >&2; return 1; }
    if [ -L "$SCRIPT_DIR/.venv/lib64" ]; then
      [ "$(readlink "$SCRIPT_DIR/.venv/lib64")" = lib ] ||
        { echo "Unsafe Python virtual environment contents." >&2; return 1; }
    elif [ -e "$SCRIPT_DIR/.venv/lib64" ]; then
      [ -d "$SCRIPT_DIR/.venv/lib64" ] ||
        { echo "Unsafe Python virtual environment contents." >&2; return 1; }
    fi
    for python_lib in "$SCRIPT_DIR/.venv/lib"/python* \
        "$SCRIPT_DIR/.venv/lib64"/python*; do
      [ -e "$python_lib" ] || continue
      [ ! -L "$python_lib" ] && [ -d "$python_lib" ] ||
        { echo "Unsafe Python virtual environment contents." >&2; return 1; }
      site_packages="$python_lib/site-packages"
      [ ! -L "$site_packages" ] &&
        { [ ! -e "$site_packages" ] || [ -d "$site_packages" ]; } ||
        { echo "Unsafe Python virtual environment contents." >&2; return 1; }
    done
    for interpreter in "$SCRIPT_DIR/.venv/bin"/python*; do
      [ -L "$interpreter" ] || continue
      interpreter_link=$(readlink "$interpreter")
      case "$interpreter_link" in
        ''|../*|*/../*)
          echo "Unsafe Python virtual environment interpreter." >&2; return 1;;
      esac
      if [ "${interpreter##*/}" = python ]; then
        case "$interpreter_link" in
          /*|*/*) echo "Unsafe Python virtual environment interpreter." >&2; return 1;;
        esac
      fi
    done
  fi
}

venv_complete() {
  [ -d "$SCRIPT_DIR/.venv/bin" ] &&
    [ -f "$SCRIPT_DIR/.venv/pyvenv.cfg" ] &&
    [ -x "$SCRIPT_DIR/.venv/bin/python" ]
}

lock_owner() {
  path=$1
  [ ! -L "$path" ] || return 2
  if [ -f "$path" ]; then
    [ "$(wc -c <"$path")" -le 32 ] || return 2
    cat "$path"
  elif [ -d "$path" ]; then
    owner="$path/pid"
    if [ ! -e "$owner" ]; then
      printf '\n'
    elif [ ! -L "$owner" ] && [ -f "$owner" ] &&
        [ "$(wc -c <"$owner")" -le 32 ]; then
      cat "$owner"
    else
      return 2
    fi
  else
    return 1
  fi
}

begin_recovery() {
  mkdir -- "$RECOVERY" 2>/dev/null ||
    { echo "Could not start setup lock recovery." >&2; return 1; }
  RECOVERY_HELD=1
  other_recovery=0
  for candidate in "$STATE_DIR"/setup.recovery.*; do
    [ -e "$candidate" ] || continue
    [ ! -L "$candidate" ] && [ -d "$candidate" ] ||
      { echo "Unsafe setup lock recovery state." >&2; return 1; }
    candidate_pid=${candidate##*.}
    case "$candidate_pid" in ''|*[!0-9]*)
      echo "Unsafe setup lock recovery owner." >&2; return 1;;
    esac
    if ! kill -0 "$candidate_pid" 2>/dev/null; then
      rm -rf -- "$candidate"
    elif [ "$candidate_pid" != "$$" ]; then
      other_recovery=1
    fi
  done
  if [ "$other_recovery" -eq 1 ]; then
    echo "Setup lock recovery is already running. Retry shortly." >&2
    return 1
  fi
  if [ -e "$LEGACY_RECOVERY" ] || [ -L "$LEGACY_RECOVERY" ]; then
    [ ! -L "$LEGACY_RECOVERY" ] &&
      { [ -f "$LEGACY_RECOVERY" ] || [ -d "$LEGACY_RECOVERY" ]; } ||
      { echo "Unsafe setup lock recovery state." >&2; return 1; }
    rm -rf -- "$LEGACY_RECOVERY"
  fi
  pid=$(lock_owner "$LOCK" 2>/dev/null || true)
  case "$pid" in
    ''|*[!0-9]*) ;;
    *) if kill -0 "$pid" 2>/dev/null; then
      echo "Setup is already running. Retry after it finishes." >&2
      return 1
    fi;;
  esac
  [ ! -L "$LOCK" ] || { echo "Unsafe setup lock." >&2; return 1; }
  rm -rf -- "$LOCK"
  if ! ln "$OWNER" "$LOCK" 2>/dev/null; then
    echo "Setup lock changed. Retry shortly." >&2
    return 1
  fi
  rm -f -- "$OWNER"
  LOCK_HELD=1
  rm -rf -- "$RECOVERY"
  RECOVERY_HELD=0
}

acquire_lock() {
  mkdir -p -- "$STATE_DIR"
  [ ! -L "$STATE_DIR" ] && [ -d "$STATE_DIR" ] || { echo "Unsafe launcher state path." >&2; return 1; }
  chmod 700 "$STATE_DIR" || { echo "Could not secure launcher state path." >&2; return 1; }
  for stale_owner in "$STATE_DIR"/setup.owner.*; do
    [ -e "$stale_owner" ] || continue
    stale_pid=${stale_owner##*.}
    case "$stale_pid" in ''|*[!0-9]*) continue;; esac
    if ! kill -0 "$stale_pid" 2>/dev/null && [ ! -L "$stale_owner" ] &&
        [ -f "$stale_owner" ]; then
      rm -f -- "$stale_owner"
    fi
  done
  for active_recovery in "$STATE_DIR"/setup.recovery.*; do
    [ -e "$active_recovery" ] || continue
    recovery_pid=${active_recovery##*.}
    case "$recovery_pid" in ''|*[!0-9]*)
      echo "Unsafe setup lock recovery owner." >&2; return 1;;
    esac
    if kill -0 "$recovery_pid" 2>/dev/null; then
      echo "Setup lock recovery is running. Retry shortly." >&2
      return 1
    fi
    [ ! -L "$active_recovery" ] && [ -d "$active_recovery" ] ||
      { echo "Unsafe setup lock recovery state." >&2; return 1; }
    rm -rf -- "$active_recovery"
  done
  rm -f -- "$OWNER"
  (set -C; printf '%s\n' "$$" >"$OWNER") 2>/dev/null ||
    { echo "Could not create setup lock owner safely." >&2; return 1; }
  if ln "$OWNER" "$LOCK" 2>/dev/null &&
      [ -f "$LOCK" ] && [ "$LOCK" -ef "$OWNER" ]; then
    rm -f -- "$OWNER"
    LOCK_HELD=1
    return 0
  fi
  if [ -d "$LOCK" ] && [ ! -L "$LOCK" ]; then
    rm -f -- "$LOCK/${OWNER##*/}"
  fi
  pid=$(lock_owner "$LOCK" 2>/dev/null || true)
  case "$pid" in
    ''|*[!0-9]*) begin_recovery;;
    *) if kill -0 "$pid" 2>/dev/null; then
      echo "Setup is already running. Retry after it finishes." >&2
      return 1
    fi
    begin_recovery;;
  esac
}

release_lock() {
  if [ "$LOCK_HELD" -eq 1 ]; then
    rm -f -- "$LOCK"
    LOCK_HELD=0
  fi
}

setup() {
  venv_path_safe || return 1
  if marker_current && venv_complete &&
      python_compatible "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/.venv"; then
    echo "Dependencies already prepared."
    return 0
  fi
  acquire_lock || return 1
  if marker_current && venv_complete &&
      python_compatible "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/.venv"; then
    release_lock
    echo "Dependencies already prepared."
    return 0
  fi
  PYTHON=$(select_python) || return 1
  rm -f -- "$READY"
  expected_marker >"$MARKER"
  prepare_log || return 1
  venv_path_safe || return 1
  echo "Preparing Python environment..."
  if ! venv_complete; then
    rm -rf -- "$SCRIPT_DIR/.venv"
    "$PYTHON" -m venv "$SCRIPT_DIR/.venv" >"$LOG" 2>&1 || { failure_excerpt; return 1; }
  elif ! python_compatible "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/.venv"; then
    rm -rf -- "$SCRIPT_DIR/.venv"
    "$PYTHON" -m venv "$SCRIPT_DIR/.venv" >"$LOG" 2>&1 || { failure_excerpt; return 1; }
  fi
  venv_path_safe || return 1
  echo "Installing declared dependencies..."
  "$SCRIPT_DIR/.venv/bin/python" -m pip --disable-pip-version-check install -e "$SCRIPT_DIR" >"$LOG" 2>&1 ||
    { failure_excerpt; return 1; }
  if ! expected_marker | cmp -s - "$MARKER"; then
    echo "Dependency declarations changed during setup. Retry." >&2
    return 1
  fi
  mv -f -- "$MARKER" "$READY"
  rm -f -- "$LOG"
  release_lock
  echo "Dependencies prepared."
}

if [ "${1-}" = setup ] && [ "$#" -eq 1 ]; then setup; exit $?; fi
setup
clear_launcher_binding
exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/main.py" "$@"
