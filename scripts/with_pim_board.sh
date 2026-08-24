#!/usr/bin/env bash
set -uo pipefail

die() {
    printf 'with_pim_board: %s\n' "$*" >&2
    exit 64
}

lease_flag=""
lease_value=""
purpose=""
long_lease="false"
child=()

while [[ $# -gt 0 ]]; do
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

[[ -n "$lease_flag" ]] || die "exactly one of --for or --until is required"
[[ -n "$lease_value" ]] || die "$lease_flag requires a non-empty value"
[[ -n "$purpose" ]] || die "--purpose requires a non-empty value"
[[ ${#child[@]} -gt 0 ]] || die "child command is required after --"

if [[ "${PIM_BOARD_LOCK_HELD:-}" == "1" ]]; then
    exec "${child[@]}"
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

board_id="${PIM_BOARD_ID:-pim}"
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

exec "${lock_command[@]}" -- env PIM_BOARD_LOCK_HELD=1 "${child[@]}"
