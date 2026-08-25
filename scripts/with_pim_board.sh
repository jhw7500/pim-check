#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEADLINE_SUPERVISOR="$SCRIPT_DIR/run_with_deadline.py"
AUTOMATION_TERM_GRACE_SECONDS=1800
AUTOMATION_LEASE_RELEASE_MARGIN_SECONDS=60
AUTOMATION_CLEANUP_MARGIN_SECONDS=$((
    AUTOMATION_TERM_GRACE_SECONDS + AUTOMATION_LEASE_RELEASE_MARGIN_SECONDS
))

die() {
    printf 'with_pim_board: %s\n' "$*" >&2
    exit 64
}

lock_marker_has_coordinates() {
    [[ "${PIM_BOARD_LOCK_HELD:-}" == "1" ]] &&
        [[ "${PIM_BOARD_LOCK_OWNER_PID:-}" =~ ^[1-9][0-9]*$ ]] &&
        [[ -n "${PIM_BOARD_LOCK_SESSION:-}" ]] &&
        [[ -n "${PIM_BOARD_LOCK_BOARD_ID:-}" ]]
}

lock_owner_is_ancestor() {
    local owner_pid="$1"
    local current_pid="$PPID"
    local stat_line stat_tail parent_pid

    while [[ "$current_pid" =~ ^[1-9][0-9]*$ ]] && ((current_pid > 1)); do
        [[ "$current_pid" == "$owner_pid" ]] && return 0
        [[ -r "/proc/$current_pid/stat" ]] || return 1
        IFS= read -r stat_line < "/proc/$current_pid/stat" || return 1
        stat_tail="${stat_line##*) }"
        parent_pid="${stat_tail#* }"
        parent_pid="${parent_pid%% *}"
        current_pid="$parent_pid"
    done
    return 1
}

lock_owner_runs_board_with() {
    local owner_pid="$1"
    local expected_board="$2"
    local -a owner_arguments=()
    local index

    [[ -r "/proc/$owner_pid/cmdline" ]] || return 1
    mapfile -d '' -t owner_arguments < "/proc/$owner_pid/cmdline" || return 1
    for ((index = 0; index + 2 < ${#owner_arguments[@]}; index++)); do
        if [[ "${owner_arguments[$index]}" == "board" ]] &&
            [[ "${owner_arguments[$((index + 1))]}" == "with" ]] &&
            [[ "${owner_arguments[$((index + 2))]}" == "$expected_board" ]]; then
            return 0
        fi
    done
    return 1
}

active_lease_matches_marker() {
    lock_marker_has_coordinates || return 1

    local owner_pid="$PIM_BOARD_LOCK_OWNER_PID"
    local lock_session="$PIM_BOARD_LOCK_SESSION"
    local lock_board_id="$PIM_BOARD_LOCK_BOARD_ID"
    local status_json

    [[ "$lock_board_id" == "$board_id" ]] || return 1
    lock_owner_is_ancestor "$owner_pid" || return 1
    lock_owner_runs_board_with "$owner_pid" "$lock_board_id" || return 1
    status_json=$("$control_bin" board status "$lock_board_id" 2>/dev/null) || return 1
    python3 -c '
import json
import sys

session, board_id = sys.argv[1:]
try:
    boards = json.load(sys.stdin)["result"]["boards"]
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
matched = any(
    board.get("board_id") == board_id
    and any(
        holder.get("session") == session
        and holder.get("mode") == "exclusive"
        and holder.get("liveness") == "alive"
        and holder.get("expired") is False
        for holder in board.get("holders", [])
    )
    for board in boards
)
raise SystemExit(0 if matched else 1)
' "$lock_session" "$lock_board_id" <<< "$status_json"
}

check_held_only="false"
if [[ $# -eq 1 && "$1" == "--check-held" ]]; then
    check_held_only="true"
fi

lease_flag=""
lease_value=""
purpose=""
long_lease="false"
child=()

while [[ "$check_held_only" == "false" && $# -gt 0 ]]; do
    case "$1" in
        --for|--until)
            [[ $# -ge 2 ]] || die "$1 requires a value"
            [[ -z "$lease_flag" ]] || die "exactly one of --for or --until is required"
            lease_flag="$1"
            lease_value="$2"
            shift 2
            ;;
        --purpose)
            [[ $# -ge 2 ]] || die "--purpose requires a value"
            purpose="$2"
            shift 2
            ;;
        --long-lease)
            [[ $# -ge 2 ]] || die "--long-lease requires the exact literal true"
            [[ "$2" == "true" ]] || die "--long-lease requires the exact literal true"
            long_lease="true"
            shift 2
            ;;
        --)
            shift
            child=("$@")
            break
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

if [[ "$check_held_only" == "false" ]]; then
    [[ -n "$lease_flag" ]] || die "exactly one of --for or --until is required"
    [[ -n "$lease_value" ]] || die "$lease_flag requires a non-empty value"
    [[ -n "$purpose" ]] || die "--purpose requires a non-empty value"
    [[ ${#child[@]} -gt 0 ]] || die "child command is required after --"
elif ! lock_marker_has_coordinates; then
    exit 1
fi

[[ -n "${JHW_CONTROL_ENV:-}" || -n "${HOME:-}" ]] || die "HOME or JHW_CONTROL_ENV is required"
control_env="${JHW_CONTROL_ENV:-$HOME/.config/jhw-control/control.env}"
[[ -r "$control_env" ]] || die "control environment is not readable: $control_env"

set -a
# shellcheck disable=SC1090
if ! source "$control_env"; then
    set +a
    die "failed to load control environment: $control_env"
fi
set +a

[[ -n "${JHW_CONTROL_BIN:-}" || -n "${HOME:-}" ]] || die "HOME or JHW_CONTROL_BIN is required"
control_bin="${JHW_CONTROL_BIN:-$HOME/.local/bin/jhw-control}"
[[ -x "$control_bin" ]] || die "jhw-control is not executable: $control_bin"

board_id="${PIM_BOARD_ID:-pim}"
if active_lease_matches_marker; then
    [[ "$check_held_only" == "true" ]] && exit 0
    exec "${child[@]}"
fi
[[ "$check_held_only" == "true" ]] && exit 1

if [[ -n "${PIM_BOARD_SESSION:-}" ]]; then
    board_session="$PIM_BOARD_SESSION"
elif [[ -n "${GITHUB_RUN_ID:-}" ]]; then
    board_session="github:${GITHUB_REPOSITORY:-pim-check}:${GITHUB_RUN_ID}:${GITHUB_RUN_ATTEMPT:-1}"
elif [[ -n "${CODEX_THREAD_ID:-}" ]]; then
    board_session="codex:${CODEX_THREAD_ID}"
elif [[ -n "${CODEX_SESSION_ID:-}" ]]; then
    board_session="codex:${CODEX_SESSION_ID}"
else
    board_session="local:${USER:-unknown}:${BASHPID}"
fi

lock_command=(
    "$control_bin" board with "$board_id"
    --mode exclusive
    "$lease_flag" "$lease_value"
    --session "$board_session"
    --purpose "$purpose"
)
if [[ "$long_lease" == "true" ]]; then
    lock_command+=(--long-lease true)
fi

lock_child=("${child[@]}")
if [[ "$long_lease" == "true" ]]; then
    [[ -x "$DEADLINE_SUPERVISOR" ]] || die \
        "deadline supervisor is not executable: $DEADLINE_SUPERVISOR"
    lease_deadline_epoch=0
    if [[ "$lease_flag" == "--for" && "$lease_value" =~ ^([1-9][0-9]{0,4})([mh])$ ]]; then
        lease_minutes="${BASH_REMATCH[1]}"
        if [[ "${BASH_REMATCH[2]}" == "h" ]]; then
            lease_minutes=$((lease_minutes * 60))
        fi
        lease_deadline_epoch=$(( $(date +%s) + lease_minutes * 60 ))
    elif [[ "$lease_flag" == "--until" ]]; then
        lease_deadline_epoch=$(date -d "$lease_value" +%s 2>/dev/null) || lease_deadline_epoch=0
    fi
    lock_child=(
        "$DEADLINE_SUPERVISOR"
        --deadline-epoch "$lease_deadline_epoch"
        --cleanup-margin-seconds "$AUTOMATION_CLEANUP_MARGIN_SECONDS"
        --term-grace-seconds "$AUTOMATION_TERM_GRACE_SECONDS"
        -- "${child[@]}"
    )
fi

exec "${lock_command[@]}" -- env \
    PIM_BOARD_LOCK_HELD=1 \
    "PIM_BOARD_LOCK_OWNER_PID=$BASHPID" \
    "PIM_BOARD_LOCK_SESSION=$board_session" \
    "PIM_BOARD_LOCK_BOARD_ID=$board_id" \
    "${lock_child[@]}"
