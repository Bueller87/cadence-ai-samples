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
GRADLE_USER_HOME="$STATE_DIR/gradle-home"
GRADLE_CACHE_READY="$GRADLE_USER_HOME/prepared.v1"
GRADLE_CACHE_MARKER="$GRADLE_USER_HOME/prepared.tmp.$$"
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
  rm -f -- "$MARKER" "$GRADLE_CACHE_MARKER" "$LOG" "$OWNER"
  remove_snapshot
  if [ "$LOCK_HELD" -eq 1 ]; then rm -f -- "$LOCK"; fi
  if [ "$RECOVERY_HELD" -eq 1 ]; then rm -rf -- "$RECOVERY"; fi
}
trap cleanup EXIT HUP INT TERM

fingerprint() { cksum "$1" | awk '{print $1 ":" $2}'; }

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
  printf 'version=1\nlauncher=%s\n' "$launcher"
  for dependency in build.gradle.kts settings.gradle.kts gradle/wrapper/gradle-wrapper.properties gradle/wrapper/gradle-wrapper.jar; do
    [ -f "$SCRIPT_DIR/$dependency" ] &&
      printf 'dependency=%s:%s\n' "$dependency" "$(fingerprint "$SCRIPT_DIR/$dependency")"
  done
  return 0
}

valid_state_path() {
  [ ! -L "$STATE_DIR" ] && { [ ! -e "$STATE_DIR" ] || [ -d "$STATE_DIR" ]; } &&
    [ ! -L "$READY" ] && { [ ! -e "$READY" ] || [ -f "$READY" ]; }
}

marker_current() {
  valid_state_path || return 1
  [ -f "$READY" ] && [ "$(wc -c <"$READY")" -le 4096 ] || return 1
  expected_marker | cmp -s - "$READY"
}

set_gradle_environment() {
  unset GRADLE_OPTS JAVA_OPTS JAVA_TOOL_OPTIONS _JAVA_OPTIONS JDK_JAVA_OPTIONS
  export GRADLE_USER_HOME
}

gradle_cache_current() {
  [ ! -L "$STATE_DIR" ] && [ -d "$STATE_DIR" ] &&
    [ ! -L "$GRADLE_USER_HOME" ] && [ -d "$GRADLE_USER_HOME" ] &&
    [ ! -L "$GRADLE_USER_HOME/caches" ] &&
    [ -d "$GRADLE_USER_HOME/caches" ] &&
    [ ! -L "$GRADLE_USER_HOME/wrapper" ] &&
    [ -d "$GRADLE_USER_HOME/wrapper" ] &&
    [ ! -L "$GRADLE_USER_HOME/wrapper/dists" ] &&
    [ -d "$GRADLE_USER_HOME/wrapper/dists" ] &&
    [ ! -L "$GRADLE_CACHE_READY" ] && [ -f "$GRADLE_CACHE_READY" ] &&
    [ "$(wc -c <"$GRADLE_CACHE_READY")" -le 128 ] &&
    printf 'version=1\ncache=gradle\n' | cmp -s - "$GRADLE_CACHE_READY"
}

prepare_gradle_cache() {
  mkdir -p -- "$STATE_DIR"
  [ ! -L "$STATE_DIR" ] && [ -d "$STATE_DIR" ] ||
    { echo "Unsafe launcher state path." >&2; return 1; }
  [ ! -L "$GRADLE_USER_HOME" ] &&
    { [ ! -e "$GRADLE_USER_HOME" ] || [ -d "$GRADLE_USER_HOME" ]; } ||
    { echo "Unsafe Gradle cache path." >&2; return 1; }
  for path in \
    "$GRADLE_USER_HOME/caches" \
    "$GRADLE_USER_HOME/wrapper" \
    "$GRADLE_USER_HOME/wrapper/dists"
  do
    [ ! -L "$path" ] &&
      { [ ! -e "$path" ] || [ -d "$path" ]; } ||
      { echo "Unsafe Gradle cache path." >&2; return 1; }
  done
  mkdir -p -- "$GRADLE_USER_HOME"
  chmod 700 "$STATE_DIR" "$GRADLE_USER_HOME"
}

verify_java() {
  command -v java >/dev/null 2>&1 || { echo "Java is required." >&2; return 1; }
  required=$(sed -n 's/.*JavaLanguageVersion\.of(\([0-9][0-9]*\)).*/\1/p' \
    "$SCRIPT_DIR/build.gradle.kts" | head -n 1)
  version=$(java -version 2>&1 | awk -F'"' 'NR == 1 { print $2 }')
  major=${version%%.*}
  if [ "$major" = 1 ]; then
    remainder=${version#*.}
    major=${remainder%%.*}
  fi
  case "$required:$major" in
    [0-9]*:[0-9]*) ;;
    *) echo "Could not verify the Java version required by build.gradle.kts." >&2; return 1;;
  esac
  if [ "$major" -lt "$required" ]; then
    echo "Installed Java $version does not satisfy Java $required." >&2
    return 1
  fi
}

failure_excerpt() {
  echo "Setup failed. Diagnostic excerpt:" >&2
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
  set_gradle_environment
  verify_java || return 1
  [ -f "$SCRIPT_DIR/gradlew" ] && [ -x "$SCRIPT_DIR/gradlew" ] && [ ! -L "$SCRIPT_DIR/gradlew" ] ||
    { echo "The checked-in Gradle wrapper is missing, non-executable, or unsafe." >&2; return 1; }
  marker_current && gradle_cache_current &&
    { echo "Dependencies already prepared."; return 0; }
  acquire_lock || return 1
  marker_current && gradle_cache_current &&
    { release_lock; echo "Dependencies already prepared."; return 0; }
  prepare_gradle_cache || return 1
  rm -f -- "$READY" "$GRADLE_CACHE_READY"
  expected_marker >"$MARKER"
  prepare_log || return 1
  echo "Preparing and compiling the Java project..."
  (cd "$SCRIPT_DIR" && ./gradlew --no-daemon classes) >"$LOG" 2>&1 || { failure_excerpt; return 1; }
  if ! expected_marker | cmp -s - "$MARKER"; then
    echo "Dependency declarations changed during setup. Retry." >&2
    return 1
  fi
  [ -d "$GRADLE_USER_HOME/caches" ] &&
    [ ! -L "$GRADLE_USER_HOME/caches" ] &&
    [ -d "$GRADLE_USER_HOME/wrapper/dists" ] &&
    [ ! -L "$GRADLE_USER_HOME/wrapper" ] &&
    [ ! -L "$GRADLE_USER_HOME/wrapper/dists" ] ||
    { echo "Gradle preparation did not create safe cache directories." >&2; return 1; }
  rm -f -- "$GRADLE_CACHE_MARKER"
  (set -C; printf 'version=1\ncache=gradle\n' >"$GRADLE_CACHE_MARKER") 2>/dev/null ||
    { echo "Could not publish Gradle cache readiness safely." >&2; return 1; }
  [ ! -L "$GRADLE_CACHE_MARKER" ] && [ -f "$GRADLE_CACHE_MARKER" ] ||
    { echo "Unsafe Gradle cache readiness path." >&2; return 1; }
  mv -f -- "$GRADLE_CACHE_MARKER" "$GRADLE_CACHE_READY"
  mv -f -- "$MARKER" "$READY"
  rm -f -- "$LOG"
  release_lock
  echo "Dependencies prepared."
}

quote_gradle_arg() {
  escaped=$(printf '%s_' "$1" | sed "s/'/'\\\"'\\\"'/g")
  escaped=${escaped%_}
  printf "'%s'" "$escaped"
}

gradle_args=
for argument in "$@"; do
  quoted=$(quote_gradle_arg "$argument")
  gradle_args="${gradle_args:+$gradle_args }$quoted"
done

if [ "${1-}" = setup ] && [ "$#" -eq 1 ]; then setup; exit $?; fi
setup
clear_launcher_binding
cd "$SCRIPT_DIR"
exec ./gradlew --no-daemon run "--args=$gradle_args"
