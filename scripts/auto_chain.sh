#!/bin/bash
# auto_chain.sh — rerun_priority 끝나면 자동으로 다음 plan들 chain 실행
# 순서: smoke → comprehensive → release_next → nightly
# 종료 시각 마커 작성: /tmp/pim-check-auto-chain-done.flag

set -u
PROJECT=/home/jhw/ai/opencode/projects/pim-check
cd "$PROJECT" || exit 1

SESSION_TS=$(date +%Y%m%d_%H%M%S)
SESSION_DIR=$PROJECT/reports/auto-chain/$SESSION_TS
mkdir -p "$SESSION_DIR"
MAIN_LOG=$SESSION_DIR/main.log

log() { echo "[$(date -Iseconds)] $*" | tee -a "$MAIN_LOG"; }

# Wait for any running pim_check plan to finish
log "=== auto_chain session $SESSION_TS started ==="
log "Waiting for any current pim_check.py to finish..."
while pgrep -f "pim_check.py.*--plan" > /dev/null 2>&1; do
    sleep 60
done
log "previous plan finished, starting chain"

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
