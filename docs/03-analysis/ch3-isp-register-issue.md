# CH3 단독 활성화 시 ISP 레지스터 반영 누락 이슈

**발견일**: 2026-04-20
**영향 케이스**: 4건 (gen_720p_ch3_vflip_on, gen_720p_ch3_ae_off, gen_fhd_ch3_vflip_on, gen_fhd_ch3_ae_off)
**상태**: 드라이버/gstApp 측 이슈 추정 — 추가 조사 필요

---

## 요약

`ch3`만 단독 활성화 (ch2는 비활성)한 상태에서 `edgeconf_pim.json`의 `vflip=true` 또는 `ae_on=false` 설정이 AP1302 ISP 레지스터에 **반영되지 않음**. ch0/ch1/ch2 단독 또는 조합은 정상 동작.

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

## 대조 실험 결과 (정상 케이스)

| 조합 | bus | 주소 | 설정 반영 | 결과 |
|------|-----|------|----------|------|
| ch0 단독 활성 + vflip_on | i2c-2 | 0x3c (single) | YES | PASS |
| ch0+ch1 dual + vflip_on | i2c-2 | 0x11/0x12 | YES | PASS |
| ch1 단독 활성 + vflip_on | i2c-2 | 0x3c (single) | YES | PASS |
| ch2 단독 활성 + vflip_on | i2c-1 | 0x3c (single) | YES | PASS |
| **ch3 단독 활성 + vflip_on** | **i2c-1** | **0x3c (single)** | **NO** | **FAIL** |
| ch2+ch3 dual + vflip_on | i2c-1 | 0x11/0x12 | (미검증 — 이번 테스트는 ch3 단독) | — |

**핵심 패턴**: ch3만 활성, ch2는 비활성 상태에서만 실패.

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

i2c-1의 드라이버 인스턴스 관점:
- 내부 ch0 = edgeconf ch2
- 내부 ch1 = edgeconf ch3

ch3 단독 활성 시 single mode로 진입하여 0x3c에 쓰기 수행. **그런데 쓰여지는 값이 내부 ch0(edgeconf ch2)의 값인지, 내부 ch1(edgeconf ch3)의 값인지가 불분명**.

추정 시나리오:
1. 드라이버가 `ctrl_cache.ch0` (edgeconf ch2의 설정 = vflip=false/ae_on=true)를 0x3c에 씀
2. ch3의 `ctrl_cache.ch1`은 **0x3c에 쓰지 않거나 먼저 쓴 후 ch0 값으로 덮어씀**
3. 결과: ISP는 ch2의 기본값으로 설정됨 (ROTATION=0x0000, AE_CTRL=0x0299)

ch2 단독 케이스는 정상 동작하는 이유는 `ctrl_cache.ch0`이 edgeconf ch2의 설정을 정확히 반영하기 때문.

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

## 영향도

| 시나리오 | 영향 |
|----------|------|
| ch0, ch1, ch2 활용 | 영향 없음 |
| ch3 단독 운영 | **vflip/ae 설정 사용 불가** |
| ch2+ch3 조합 | 미검증 (추정: 정상) |
| 4ch 모두 활성 | 미검증 (추정: 정상) |

---

## 권장 조치

1. **gstApp 또는 드라이버 측 조사**: i2c-1 bus에서 ch3 단독 활성 시 `ctrl_cache.ch1` (내부 매핑)의 레지스터 쓰기 경로 확인
2. **임시 우회**: ch3 사용이 필요한 경우 ch2도 함께 활성화 (dual mode)
3. **QA 커버리지 유지**: 해결 전까지 `gen_{res}_ch3_vflip_on`, `gen_{res}_ch3_ae_off` 4건은 **알려진 실패**로 `known_issues` 분류 권장

---

## 관련 파일
- 테스트 케이스: `/home/jhw/ai/opencode/projects/pim-check/profiles/generated/gen_{720p,fhd}_ch3_{vflip_on,ae_off}.yaml`
- 스키마: `/home/jhw/ai/opencode/projects/pim-check/profiles/schema.yaml` (vflip_ch3, ae_ch3 축)
- 실행 결과: `/home/jhw/ai/opencode/projects/pim-check/channel_retry_results.json`
- 실행 스크립트: `/home/jhw/ai/opencode/projects/pim-check/run_failed_retry.py`
- 드라이버 코드: `/home/jhw/ai/opencode/projects/max9296/max9296.c`
