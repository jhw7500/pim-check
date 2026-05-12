#!/bin/bash
# auto_weekend.sh — 주말 자동 검증 (월요일 09:00까지 반복)
# 각 round: smoke → comprehensive → nightly → release_next
# 결과: reports/auto-weekend/{session_ts}/{plan}-r{round}.log + 각 plan reports/{plan}/{ts}.{html,json,xml,md}

set -u

PROJECT=/home/jhw/ai/opencode/projects/pim-check
cd "$PROJECT" || exit 1

SESSION_TS=$(date +%Y%m%d_%H%M%S)
SESSION_DIR=$PROJECT/reports/auto-weekend/$SESSION_TS
mkdir -p "$SESSION_DIR"

# 월요일 09:00 KST까지 진행 (이번 주 월요일)
DEADLINE=$(date -d 'next monday 09:00' +%s 2>/dev/null || date -d 'monday 09:00' +%s)

PLANS=(smoke comprehensive nightly release_next)

MAIN_LOG=$SESSION_DIR/main.log
STATUS_FLAG=$SESSION_DIR/status

log() {
  echo "[$(date -Iseconds)] $*" | tee -a "$MAIN_LOG"
}

log "=== auto_weekend session $SESSION_TS ==="
log "session_dir: $SESSION_DIR"
log "deadline:    $(date -d "@$DEADLINE" -Iseconds)"
log "remaining:   $(( (DEADLINE - $(date +%s)) / 3600 ))h"
log "plans:       ${PLANS[*]}"
log ""

ROUND=0
while [ $(date +%s) -lt $DEADLINE ]; do
  ROUND=$((ROUND + 1))
  log "========== ROUND $ROUND start =========="
  for plan in "${PLANS[@]}"; do
    if [ $(date +%s) -ge $DEADLINE ]; then
      log "deadline reached, stopping mid-round"
      break 2
    fi
    PLAN_LOG=$SESSION_DIR/${plan}-r${ROUND}.log
    log "[round $ROUND][$plan] start ($(date +%H:%M:%S))"
    PLAN_START=$(date +%s)
    python3 pim_check.py --plan "$plan" --log >> "$PLAN_LOG" 2>&1
    PLAN_EXIT=$?
    PLAN_DUR=$(( $(date +%s) - PLAN_START ))
    log "[round $ROUND][$plan] done exit=$PLAN_EXIT dur=${PLAN_DUR}s"
  done
  log "========== ROUND $ROUND done =========="
  log ""
done

log "=== auto_weekend complete at $(date -Iseconds) ==="
log "rounds completed: $ROUND"
echo "DONE rounds=$ROUND deadline=$(date -d "@$DEADLINE" -Iseconds)" > "$STATUS_FLAG"
