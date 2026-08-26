#!/bin/bash
# auto_overnight.sh — 지금~내일 09:00 KST plan chain (비중복)
#
# 순서 (시간 분배 ~15h):
#   1. nightly         ~8h   전수 회귀
#   2. channel_verify  ~4h   단일 토글 detail (vflip/hflip/ae)
#   3. comprehensive   ~1.5h release candidate (multi 16건)
#   4. release_next    ~35m  release gate
#   5. smoke loop      남는 시간 매 25분
#
# 09:00 KST에 자동 종료

set -u
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=$(cd -- "$SCRIPT_DIR/.." && pwd)
SCRIPT_PATH="$SCRIPT_DIR/$(basename -- "${BASH_SOURCE[0]}")"
BOARD_WRAPPER="$SCRIPT_DIR/with_pim_board.sh"
TARGET_END=${PIM_AUTOMATION_TARGET_END:-$(date -d 'tomorrow 09:00' +%s)}
TARGET_UNTIL=$(date -d "@$TARGET_END" -Iseconds)
LEASE_PURPOSE="pim-check auto_overnight"

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
SESSION_DIR=$PROJECT/reports/auto-overnight/$SESSION_TS
mkdir -p "$SESSION_DIR"
MAIN_LOG=$SESSION_DIR/main.log

log() { echo "[$(date -Iseconds)] $*" | tee -a "$MAIN_LOG"; }

log "=== auto_overnight session start ==="
log "target end: $(date -d "@$TARGET_END" -Iseconds)"

# 순차 plan 실행 (각 plan 종료까지 wait)
PLANS=(nightly channel_verify comprehensive release_next)
for plan in "${PLANS[@]}"; do
    if [ "$(date +%s)" -ge "$TARGET_END" ]; then
        log "TARGET reached before $plan — skip"
        break
    fi
    log "[$plan] START"
    START=$(date +%s)
    python3 pim_check.py --plan "$plan" --log > "$SESSION_DIR/${plan}.log" 2>&1
    EXIT=$?
    DUR=$(( $(date +%s) - START ))
    log "[$plan] DONE exit=$EXIT dur=${DUR}s"
done

# 남는 시간 smoke loop until 09:00
log "Entering smoke loop (until 09:00)"
N=1
while [ "$(date +%s)" -lt "$TARGET_END" ]; do
    REMAIN=$(( TARGET_END - $(date +%s) ))
    if [ $REMAIN -lt 1800 ]; then
        log "Remaining ${REMAIN}s < smoke time. Stop loop."
        break
    fi
    log "[smoke #$N] START remain=${REMAIN}s"
    START=$(date +%s)
    python3 pim_check.py --plan smoke --log > "$SESSION_DIR/smoke-$(printf %02d $N).log" 2>&1
    EXIT=$?
    DUR=$(( $(date +%s) - START ))
    log "[smoke #$N] DONE exit=$EXIT dur=${DUR}s"
    N=$((N+1))
done

log "=== auto_overnight complete ==="
echo "DONE plans=${PLANS[*]} smoke=$((N-1))" > /tmp/pim-check-overnight-done.flag
log "Final flag: /tmp/pim-check-overnight-done.flag"
