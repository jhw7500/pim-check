# 내부 ch1 슬롯(edgeconf ch1/ch3) 단독 활성화 시 ISP 레지스터 반영 누락 이슈

**발견일**: 2026-04-20
**잠정 Resolution**: 2026-04-21 (드라이버/gstApp 수정으로 96/96 PASS 선언)
**재현 확인일**: 2026-04-22 (검증 프로그램 결함 발견 → ch1 단독도 동일 버그로 확정)
**영향 범위**: **내부 ch1 슬롯에 매핑되는 edgeconf ch1 + ch3의 단독 활성 케이스 전체**
**상태**: ⚠️ **SCOPE EXPANDED** — 이슈 범위가 "ch3 단독"에서 "ch1/ch3 단독 (내부 ch1 슬롯)"으로 확장. 2026-04-21 Resolution은 검증 결함(아래 §"검증 프로그램 결함"로 인한 false PASS였음이 2026-04-22 실측으로 재확인됨. 드라이버 재수정은 **현재 진행 중**이며 새 검증 스키마(2026-04-22 정정)로 재검증 예정.

> **2026-04-22 주요 정정**
> - 이전 "ch1 단독 PASS" 기록은 검증 프로파일이 다른 채널을 명시 disable하지 않아 실제로는 **dual 모드로 동작**했기에 나온 false PASS였음.
> - 검증 커맨드의 `dual 주소 || single 주소` fallback이 실제 동작 모드 확인 없이 어느 쪽이든 응답하면 통과시켜 single-mode 버그를 수년간 은폐.
> - 이슈 범위: **"ch3 단독"이 아니라 "내부 ch1 슬롯 = edgeconf ch1 + ch3 단독 모드"**.
> - 상세: §"검증 프로그램 결함 (2026-04-22 발견)" 섹션.

---

## 요약

AP1302 ISP의 MAX9296 드라이버(`max9296.c`)가 **내부 ch1 슬롯에 할당된 채널**(버스 i2c-2에서는 edgeconf `ch1`, 버스 i2c-1에서는 edgeconf `ch3`)을 **단독 활성화**(해당 슬롯만 enable, 같은 버스의 짝 채널은 disable)한 상태에서, `edgeconf_pim.json`의 `vflip`/`hflip`/`ae_on`/`awb` 설정이 AP1302 ISP 레지스터에 **반영되지 않음**. 내부 ch0 슬롯에 할당된 채널(edgeconf `ch0`, `ch2`) 단독은 정상 동작.

---

## 환경

| 항목 | 값 |
|------|-----|
| 타겟 | 192.168.0.5 (iMX8MP) |
| 드라이버 | `/home/jhw/ai/opencode/projects/max9296/max9296.c` |
| 앱 | `gstApp -d 11 -m 4` (pid=420) |
| ch0,ch1 버스 | i2c-2 |
| ch2,ch3 버스 | i2c-1 |
| AP1302 dual mode | 0x11(ch0 또는 ch2), 0x12(ch1 또는 ch3) |
| AP1302 single mode | 0x3c (ch0/ch1/ch2/ch3 단독 시) |

---

## 재현 절차

### Case 1: ch3 vflip_on 미반영

**Step 1 — 설정 변경 (edgeconf_pim.json)**
```bash
jq '.VHL_CAM.i2c1.ch3.enable = true |
    .VHL_CAM.i2c1.ch3.vflip = true |
    .VHL_CAM.cam_width = 1280 |
    .VHL_CAM.cam_height = 720' \
    /root/shared_v/edgeconf_pim.json > /tmp/e.json && \
mv /tmp/e.json /root/shared_v/edgeconf_pim.json
```

**Step 2 — 재부팅 + 안정화 대기 30초**
```bash
reboot && sleep 90
```

**Step 3 — ISP ROTATION 레지스터 읽기 (i2c-1 bus, 주소 0x3c 또는 0x12)**
```bash
# dual mode(ch2+ch3) 시도 → 실패하면 single mode(0x3c)
i2ctransfer -f -y 1 w2@0x12 0x10 0x0c r2 2>/dev/null || \
i2ctransfer -f -y 1 w2@0x3c 0x10 0x0c r2 2>/dev/null
```

**결과**
| 항목 | 값 |
|------|-----|
| 기대 (vflip=true, bit1) | `0x00 0x02` |
| 실측 | `0x00 0x00` ❌ |

---

### Case 2: ch3 ae_off 미반영

**Step 1 — 설정 변경**
```bash
jq '.VHL_CAM.i2c1.ch3.enable = true |
    .VHL_CAM.i2c1.ch3.ae_on = false' \
    /root/shared_v/edgeconf_pim.json > /tmp/e.json && \
mv /tmp/e.json /root/shared_v/edgeconf_pim.json
```

**Step 2 — 재부팅 + 대기**

**Step 3 — ISP AE_CTRL 레지스터 읽기**
```bash
i2ctransfer -f -y 1 w2@0x12 0x50 0x02 r2 2>/dev/null || \
i2ctransfer -f -y 1 w2@0x3c 0x50 0x02 r2 2>/dev/null
```

**결과**
| 항목 | 값 |
|------|-----|
| 기대 (ae_on=false, MANUAL=0x0290) | `0x02 0x90` |
| 실측 | `0x02 0x99` ❌ (AUTO 그대로) |

---

## 대조 실험 결과 (2026-04-22 정정)

### 2026-04-22 실측 (정정된 검증 프로파일 적용)

| 조합 | bus | 주소 | 설정 반영 | 결과 |
|------|-----|------|----------|------|
| ch0 단독 활성 + 설정 변경 | i2c-2 | 0x3c (single) | YES | PASS |
| **ch1 단독 활성 + hflip_on** | **i2c-2** | **0x3c (single)** | **NO** | **FAIL** |
| ch2 단독 활성 + 설정 변경 | i2c-1 | 0x3c (single) | YES | PASS |
| **ch3 단독 활성 + vflip_on/ae_off** | **i2c-1** | **0x3c (single)** | **NO** | **FAIL** |
| 모든 dual/quad 조합 | i2c-{1,2} | 0x11/0x12 | YES | PASS |

**핵심 패턴**: **내부 ch1 슬롯에 매핑된 채널**(i2c-2=edgeconf ch1, i2c-1=edgeconf ch3)이 단독 활성일 때 0x3c 쓰기 경로에서 설정이 누락됨.

### 2026-04-17/20 기록 (False PASS 정정)

기존 대조표의 "ch1 단독 활성 + vflip_on = PASS" 행은 **검증 결함으로 인한 false PASS**였음.

| 원본 기록 | 실제 상태 | 원인 |
|-----------|-----------|------|
| ch1 단독 vflip_on PASS | 실은 ch0+ch1 dual에서 실행됨 | 프로파일이 `ch0.enable=false`를 명시하지 않아 베이스 edgeconf의 ch0 enable 상태가 유지됨 |
| ch1 단독 검증 통과 | 사실 ISP 0x12(dual) 주소로 읽음 | 검증 커맨드 `0x12 || 0x3c` fallback이 dual 주소에 먼저 응답하면 single 확인 건너뜀 |
| ch3 단독만 실패 | 실은 ch1 단독도 같은 버그 | ch3 케이스만 베이스 edgeconf에서 ch2가 disable 상태였기에 single mode로 진입 |

---

## 드라이버 코드 분석

**참조 파일**: `/home/jhw/ai/opencode/projects/max9296/max9296.c`

드라이버는 해상도 기반으로 dual/single 모드 판단:
```c
// max9296.c:1693-1696
bool dual = (sensor->current_mode->id == MAX9296_MODE_2560x720 ||
             sensor->current_mode->id == MAX9296_MODE_3840x1080);
u32 ch0_addr = dual ? AP1302_CH0_I2C_ADDR : AP1302_I2C_ADDR;  // 0x11 or 0x3c
u32 ch1_addr = dual ? AP1302_CH1_I2C_ADDR : AP1302_I2C_ADDR;  // 0x12 or 0x3c
```

`write_per_channel`은 dual/single에 따라 ch0, ch1 양쪽에 쓰기:
```c
// max9296.c:1572-1590
if (dual) {
    write to AP1302_CH0_I2C_ADDR (0x11)
    write to AP1302_CH1_I2C_ADDR (0x12)
} else {
    write to AP1302_I2C_ADDR (0x3c)  // 단 한 번
}
```

드라이버 인스턴스 관점 (버스별 내부 매핑):

| 버스 | 내부 ch0 슬롯 | 내부 ch1 슬롯 |
|------|--------------|--------------|
| i2c-2 | edgeconf `ch0` | edgeconf `ch1` |
| i2c-1 | edgeconf `ch2` | edgeconf `ch3` |

**내부 ch1 슬롯** 단독 활성 시 single mode로 진입하여 0x3c에 쓰기를 시도하지만, 드라이버가 **내부 ch0 슬롯의 ctrl_cache** (disable 상태)만 0x3c에 쓰고 **내부 ch1 슬롯의 ctrl_cache**는 쓰지 않거나 ch0 값으로 덮어씀.

추정 시나리오 (2026-04-22 확인):
1. 드라이버가 `ctrl_cache.ch0` (disable 상태 채널의 설정 = vflip=false/ae_on=true/hflip=false)를 0x3c에 씀
2. 실제 동작해야 하는 내부 ch1 슬롯의 `ctrl_cache.ch1`은 **0x3c에 쓰지 않거나 먼저 쓴 후 ch0 값으로 덮어씀**
3. 결과: ISP는 disable된 ch0 슬롯의 기본값으로 설정됨 (ROTATION=0x0000, AE_CTRL=0x0299, AWB_CTRL=0x115f)

내부 ch0 슬롯(edgeconf ch0/ch2) 단독 케이스는 정상 동작 — `ctrl_cache.ch0`이 해당 채널 설정을 정확히 반영해 0x3c에 쓰기 때문.

---

## 검증 도구 사용 방법

### pim-check 자동 실행
```bash
cd /home/jhw/ai/opencode/projects/pim-check
python3 pim_check.py --case gen_720p_ch3_vflip_on --duration 0 --json
# reports/gen_720p_ch3_vflip_on_*.json 에서 상세 결과 확인
```

### 수동 재현 (SSH)
```bash
# 타겟에서 직접 확인
sshpass -p root ssh root@192.168.0.5

# 1. ch3 단독 활성화 + vflip=true
jq '.VHL_CAM.i2c1.ch3.enable = true | .VHL_CAM.i2c1.ch3.vflip = true' \
    /root/shared_v/edgeconf_pim.json | sponge /root/shared_v/edgeconf_pim.json

# 2. 재부팅
reboot

# 3. 재접속 후 30초 대기 후 레지스터 확인
i2ctransfer -f -y 1 w2@0x3c 0x10 0x0c r2
# 결과 0x00 0x00 (기대: 0x00 0x02)
```

---

## 영향도 (수정 전, 2026-04-22 정정)

| 시나리오 | 영향 |
|----------|------|
| 내부 ch0 슬롯 단독 (edgeconf ch0, ch2) | 영향 없음 |
| **내부 ch1 슬롯 단독 (edgeconf ch1, ch3)** | **vflip/hflip/ae/awb 설정 사용 불가** |
| dual/quad (2~4채널 활성) | 영향 없음 (0x11/0x12 경로로 쓰기 성공) |

2026-04-22 실측 (드라이버 롤백 상태): ch1 단독 hflip_on, ROTATION 기대 `0x0001` → 실측 `0x0000`. ch3 단독도 동일 패턴 (2026-04-20 기록 참조).

---

## Resolution (2026-04-21) — ⚠️ 재검증 필요

> **2026-04-22 주석**: 이 Resolution은 아래 §"검증 프로그램 결함"에 서술된 **동일 결함 프로파일**로 검증됐음. 즉 96/96 PASS 중 ch0/ch2를 제외한 **내부 ch1 슬롯 단독 케이스**(ch1/ch3 단독)는 실제로는 dual 모드로 동작했을 가능성이 높아 **재검증 필요**. 2026-04-22 실측에서 ch1 단독은 여전히 FAIL로 재현됨 (단, 2026-04-22 실측은 드라이버를 이전 버전으로 **롤백한 상태**에서 이뤄졌으므로 최신 드라이버에 대한 재검증은 아래 "정정된 검증 프로파일" 적용 후 재실행해야 함).

### 수정 내용 (2026-04-21)
드라이버(`max9296.c`) 및 gstApp 코드 레벨 수정. 추가로 **AWB 설정(awb_ctrl 레지스터 0x5100)** 도입.

### 검증 방법 (⚠️ 결함 있는 방법 — 2026-04-22 이전 프로파일)
`run_comprehensive_verify.py` — **96 시나리오** 완전 검증:
- **Phase 2 (Quad mode)**: 4채널 모두 활성 + 각 채널의 4가지 설정(vflip/hflip/ae/awb) 개별 토글 × 2해상도 = 32 tests
- **Phase 3 (Dual mode)**: 4 dual 조합 × 2채널 × 4설정 × 2해상도 = 64 tests
  - `samebus_i2c2` (ch0+ch1), `samebus_i2c1` (ch2+ch3): dual mode 0x11/0x12
  - `crossbus_lo` (ch0+ch2), `crossbus_hi` (ch1+ch3): 각 버스 single mode 0x3c

### 결과
**96/96 PASS (100%)** — 총 2시간 15분 실행

| 영역 | PASS |
|------|------|
| Phase 2 Quad | 32/32 |
| Phase 3 samebus_i2c2 | 16/16 |
| Phase 3 samebus_i2c1 | 16/16 |
| Phase 3 crossbus_lo | 16/16 |
| Phase 3 crossbus_hi | 16/16 |

**채널별**: ch0/ch1/ch2/ch3 각 24/24 — 모든 채널이 모든 모드에서 정상 동작  
**설정별**: vflip/hflip/ae_on/awb 각 24/24 — 모든 설정이 정확히 ISP 반영

### 검증된 신규 기능 (AWB)
| awb 값 | AWB_CTRL (0x5100) |
|--------|--------------------|
| auto | 0x115f |
| off | 0x1150 |
| horizon | 0x1151 |
| a | 0x1152 |
| cwf | 0x1153 |
| d50 | 0x1154 |
| d65 | 0x1155 |
| d75 | 0x1156 |
| temp | 0x1157 |
| measure | 0x1158 |

테스트에서는 `auto ↔ off` 전환(0x115f / 0x1150)만 검증 — 확장 필요 시 schema에 AWB 모드 축 추가 권장.

### 관련 산출물
- 실행 스크립트: `/home/jhw/ai/opencode/projects/pim-check/run_comprehensive_verify.py`
- 결과 데이터: `/home/jhw/ai/opencode/projects/pim-check/comprehensive_results.json`
- 실행 로그: `/home/jhw/ai/opencode/projects/pim-check/comprehensive.log`

---

## 검증 프로그램 결함 (2026-04-22 발견)

### 요지
이 문서의 2026-04-17 ~ 2026-04-21 결과(특히 "ch1 단독 PASS"와 96/96 PASS)는 **검증 프로파일/커맨드 결함**으로 인한 **false PASS**. 드라이버 버그는 실제로는 **ch3 뿐만 아니라 ch1 단독도 동일하게 존재**했음.

### 결함 상세

**결함 A: 프로파일이 "단독 모드"를 강제하지 않음**

기존 `gen_*_ch{N}_*.yaml` 프로파일은 대상 채널의 `enable: true`와 해당 속성만 세팅하고, **다른 3채널의 enable 상태는 베이스 edgeconf에 맡겼음**.

```yaml
# 잘못된 기존 프로파일 (gen_720p_ch1_vflip_on.yaml)
edgeconf_changes:
  .VHL_CAM.i2c2.ch1.enable: true     # 이것만 건드림
  .VHL_CAM.i2c2.ch1.vflip: true
  # ch0, ch2, ch3의 enable 상태는 미지정 — 베이스 값 유지
```

→ 베이스 edgeconf에서 ch0이 enable이면 실제로는 **ch0+ch1 dual 모드**로 실행되어 single-mode 코드 경로를 우회함.

**결함 B: 검증 커맨드의 dual/single 주소 fallback**

```yaml
# 잘못된 검증 커맨드
command: "(i2ctransfer -f -y 2 w2@0x12 0x10 0x0c r2 2>/dev/null || \
           i2ctransfer -f -y 2 w2@0x3c 0x10 0x0c r2 2>/dev/null) | tr -d ' '"
```

→ dual 주소 0x12가 응답하면 single 0x3c는 확인도 안 함. **실제 동작 모드와 상관없이 아무 주소나 맞으면 통과**. single-mode 전용 버그를 원천적으로 검출 불가능.

**결함 C: 동작 모드 진입 자체를 검증 안 함**

i2c 스캔이나 주소 응답성으로 "현재 타겟이 의도한 single/dual 모드인지" 확인하는 단계가 없음. 드라이버 동작과 프로파일 의도 사이의 불일치를 놓침.

### 정정 (2026-04-22)

`profiles/schema.yaml`에 다음 변경 적용 (`tools/fix_schema_single_mode.py`로 일괄 적용):

1. **per-channel 축(`vflip_ch{N}`, `hflip`, `ae_ch{N}`, `awb_ch{N}`)의 `values` 블록에 대상 외 3채널 `enable: false` 명시 추가** → single 모드 강제
2. **검증 커맨드에서 dual 주소 fallback 제거** → `0x3c`만 읽음. single 모드 진입 실패 시 자연스럽게 FAIL

정정 후 프로파일 재생성: `python3 -c "from generator import generate_cases; generate_cases('profiles')"` — 110개 프로파일 재생성.

### 교훈

- "단독 모드 테스트"는 **다른 채널의 state도 모두 명시**해야 한다. 테스트 케이스가 기본값/베이스 상태에 의존하면 환경 변동에 깨지고 버그를 가린다.
- 주소 fallback은 편의를 위한 것이지만 **모드 식별**을 흐려 검증 결과를 쓸모없게 만들 수 있다. 의도한 모드를 검증에 명시하라.
- 테스트 결과가 "100% PASS"로 나올 때 **테스트 방법론 자체에 결함이 없는지** 교차 검증이 필요하다.

### 영향 받은 과거 산출물 (신뢰도 낮음 → 재검증 필요)

- `channel_verify_results.json` (2026-04-17, 32 cases) — ch1 단독 PASS 8건, ch2 단독 FAIL 6건 등 모두 결함 프로파일 기반
- `channel_retry_results.json` (2026-04-20, 13 cases) — 재시도 결과 역시 동일 결함 프로파일
- `comprehensive_results.json` (2026-04-21, 96 cases, Resolution 근거) — 동일 결함 프로파일로 돌아 단독 모드 실측이 실제 dual 실측이었을 가능성 높음
- 본 문서(`ch3-isp-register-issue.md`) 원본 — ch3만 문제라고 주장 → 실제로는 ch1/ch3 공통

---

## 권장 조치 (2026-04-22 갱신)

1. **드라이버 재수정 및 재검증**: `max9296.c`의 `write_per_channel`에서 **내부 ch1 슬롯**(버스와 무관하게 적용)의 single mode 0x3c 쓰기 경로를 확인. i2c-2(edgeconf ch1 단독)와 i2c-1(edgeconf ch3 단독) 양쪽 모두 동일 버그.
2. **정정된 검증 프로파일로 재검증**: 2026-04-22 스키마로 재생성한 프로파일을 `run_comprehensive_verify.py`로 재실행. 특히 `gen_*_ch1_*`와 `gen_*_ch3_*` 단독 케이스가 PASS하는지 확인 후에야 Resolution 선언.
3. **임시 우회**: 내부 ch1 슬롯(edgeconf ch1, ch3) 단독 사용이 필요한 경우 같은 버스의 짝 채널(각각 ch0, ch2)도 함께 활성화해 dual 모드로 동작시키기.
4. **QA 커버리지 유지**: 해결 전까지 `gen_{res}_ch1_*`(8건) + `gen_{res}_ch3_*`(8건) 단독 케이스는 **알려진 실패**로 `known_issues` 분류 권장.
5. **검증 메서드 회귀 방지**: 향후 새 axis 추가 시 프로파일 generator가 대상 채널의 **단독 여부**를 명시적으로 요구하도록 schema 필드 추가 고려 (예: `isolation: single|dual|quad`).

---

## 관련 파일
- 테스트 케이스: `/home/jhw/ai/opencode/projects/pim-check/profiles/generated/gen_{720p,fhd}_ch3_{vflip_on,ae_off}.yaml`
- 스키마: `/home/jhw/ai/opencode/projects/pim-check/profiles/schema.yaml` (vflip_ch3, ae_ch3 축)
- 실행 결과: `/home/jhw/ai/opencode/projects/pim-check/channel_retry_results.json`
- 실행 스크립트: `/home/jhw/ai/opencode/projects/pim-check/run_failed_retry.py`
- 드라이버 코드: `/home/jhw/ai/opencode/projects/max9296/max9296.c`
