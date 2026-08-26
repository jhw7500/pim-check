#!/bin/bash
# auto_chain.sh — rerun_priority 끝나면 자동으로 다음 plan들 chain 실행
# 순서: smoke → comprehensive → release_next → nightly
# 종료 시각 마커 작성: /tmp/pim-check-auto-chain-done.flag

set -u
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=$(cd -- "$SCRIPT_DIR/.." && pwd)
SCRIPT_PATH="$SCRIPT_DIR/$(basename -- "${BASH_SOURCE[0]}")"
BOARD_WRAPPER="$SCRIPT_DIR/with_pim_board.sh"
TARGET_END=${PIM_AUTOMATION_TARGET_END:-$(( $(date +%s) + 24 * 60 * 60 ))}
TARGET_UNTIL=$(date -d "@$TARGET_END" -Iseconds)
LEASE_PURPOSE="pim-check auto_chain"

if ! "$BOARD_WRAPPER" \
    --check-held \
    --until "$TARGET_UNTIL" \
    --purpose "$LEASE_PURPOSE" \
    --long-lease true; then
    export PIM_AUTOMATION_TARGET_END="$TARGET_END"
    exec "$BOARD_WRAPPER" \
        --until "$TARGET_UNTIL" \
        --purpose "$LEASE_PURPOSE" \
        --long-lease true \
        -- "$SCRIPT_PATH" "$@"
fi

cd "$PROJECT" || exit 1

SESSION_TS=$(date +%Y%m%d_%H%M%S)
SESSION_DIR=$PROJECT/reports/auto-chain/$SESSION_TS
mkdir -p "$SESSION_DIR"
MAIN_LOG=$SESSION_DIR/main.log

log() { echo "[$(date -Iseconds)] $*" | tee -a "$MAIN_LOG"; }

# Refuse a legacy unwrapped process instead of consuming this lease waiting for it.
log "=== auto_chain session $SESSION_TS started ==="
if pgrep -f "pim_check.py.*--plan" > /dev/null 2>&1; then
    log "ERROR: legacy pim_check.py --plan process is already running; refusing unsafe lease wait"
    exit 75
fi
log "no legacy plan detected, starting chain"

PLANS=(smoke comprehensive release_next nightly)
for plan in "${PLANS[@]}"; do
    PLAN_LOG=$SESSION_DIR/${plan}.log
    log "[$plan] START"
    START=$(date +%s)
    python3 pim_check.py --plan "$plan" --log >> "$PLAN_LOG" 2>&1
    EXIT=$?
    DUR=$(( $(date +%s) - START ))
    log "[$plan] DONE exit=$EXIT dur=${DUR}s"
done

log "=== auto_chain complete ==="
echo "DONE plans=${PLANS[*]}" > /tmp/pim-check-auto-chain-done.flag
