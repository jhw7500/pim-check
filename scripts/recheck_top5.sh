#!/bin/bash
# recheck_top5.sh — weekend 결과 Top 5 FAIL case 단독 재검증
# 각 case 단독 실행 + 결과/log 저장 + 비교 보고서 생성

set -u
PROJECT=/home/jhw/ai/opencode/projects/pim-check
cd "$PROJECT" || exit 1

WEEKEND_DIR=$PROJECT/reports/auto-weekend/20260515_180327
RECHECK_DIR=$WEEKEND_DIR/recheck
mkdir -p "$RECHECK_DIR"
MAIN_LOG=$RECHECK_DIR/main.log
SUMMARY=$RECHECK_DIR/SUMMARY.md

log() { echo "[$(date -Iseconds)] $*" | tee -a "$MAIN_LOG"; }

# Top 5 (weekend smoke 129회 + retry FAIL 빈도순)
CASES=(720p_4ch fhd_4ch 720p_2ch fhd_2ch_03 fhd_3ch_012)
declare -A WEEKEND_FAIL=(
    [720p_4ch]="128/257 (50%)"
    [fhd_4ch]="126/255 (49%)"
    [720p_2ch]="68/197 (35%)"
    [fhd_2ch_03]="67/196 (34%)"
    [fhd_3ch_012]="64/193 (33%)"
)

log "=== recheck Top 5 start ==="

cat > "$SUMMARY" <<EOF
# Weekend FAIL Top 5 재검증 보고서

세션: 20260515_180327 (2026-05-15 18:03 ~ 2026-05-18 08:30, 62h, 129회 smoke)
재검증 시작: $(date -Iseconds)

## Top 5 case별 weekend 빈도 vs 단독 재검증 결과

| Case | Weekend FAIL/TOTAL | 단독 재검증 | 변화 |
|---|---|---|---|
EOF

for case in "${CASES[@]}"; do
    log "[$case] START"
    START=$(date +%s)
    python3 pim_check.py --case "$case" --log > "$RECHECK_DIR/${case}.log" 2>&1
    EXIT=$?
    DUR=$(( $(date +%s) - START ))

    # 결과 추출
    RESULT_LINE=$(grep -E "^Result: " "$RECHECK_DIR/${case}.log" | head -1)
    FAILED_CHECKS=$(grep "\[X\]" "$RECHECK_DIR/${case}.log" | head -10)

    if [ "$EXIT" = "0" ]; then
        RECHECK="PASS"
        CHANGE="✅ 일시적 (재검증에서 회복)"
    else
        RECHECK="FAIL"
        CHANGE="❌ 일관 FAIL"
    fi

    log "[$case] DONE exit=$EXIT dur=${DUR}s → $RECHECK"
    echo "| $case | ${WEEKEND_FAIL[$case]} | $RECHECK | $CHANGE |" >> "$SUMMARY"
done

# 각 case별 상세 FAIL 항목
{
    echo
    echo "## case별 상세 FAIL 항목"
    echo
    for case in "${CASES[@]}"; do
        echo "### $case"
        echo '```'
        grep -E "^Result:|\[X\]" "$RECHECK_DIR/${case}.log" 2>/dev/null | head -10 || echo "(no detail)"
        echo '```'
        echo
    done
} >> "$SUMMARY"

log "=== recheck complete ==="
log "SUMMARY: $SUMMARY"
echo "DONE" > "$RECHECK_DIR/done.flag"
