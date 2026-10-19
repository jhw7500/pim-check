"""
checks/frame_flow.py - 채널별 프레임 흐름(enc_queue_input 증가율) 체크

녹화 finalize 를 기다리지 않고 "프레임이 실제로 흐르는지"를 확인한다. gstApp
camera-health 스냅샷(/run/pim-camera/gstApp.json, 1Hz 발행)의 채널별 gstreamer
observation 에 있는 enc_queue_input 카운터를 sample_sec 간격으로 두 번 읽고,
edgeconf 의 fps·채널 enable 과 대조한다. 세 읽기는 ssh 명령 하나에 묶는다.

판정:
  - PASS: enabled 채널은 카운터가 증가하고 측정 fps(=증가분 / observed_monotonic_ms
    차이)가 edgeconf fps ±fps_tolerance_pct 이내, disabled 채널은 DISABLED 보고.
  - 확정 FAIL: enabled 채널 fps 범위 이탈 / enabled 인데 DISABLED 보고 / disabled 인데
    프레임 증가 또는 DISABLED 미보고 / 카운터 역행 / 두 읽기 사이 pid·boot_id 변경 /
    edgeconf 판독 불가.
  - 과도 상태(NEED_FRAME_FLOW — verify_retry stabilization 토큰): 스냅샷 부재·JSON 깨짐·
    수집 실패, sequence 미증가, enabled 채널 카운터 미발행·미증가. 부팅 직후 창에서
    monitor_until_pass 가 stable-fail 조기 종료 없이 계속 기다리게 한다. 창이 끝나도
    지속되면(예: pim-package#191 SIGKILL 뒤 카운터 고착) merge 결과가 fail 로 남는다.
  확정 결함이 하나라도 있으면 reason 에 과도 상태를 섞지 않는다 — reason 전체에
  토큰이 있으면 확정 결함까지 '준비 중'으로 읽혀 조기 종료가 막힌다.

설정 (checks.frame_flow, 기본 OFF):
  enabled: false                       # plan case_overrides 로 켠다 (quick_verify)
  path: /run/pim-camera/gstApp.json    # null 이면 skip — 케이스 opt-out (cam_health 와 같은 관례)
  sample_sec: 3
  fps_tolerance_pct: 10
"""
from __future__ import annotations

import json

from checks.base_check import BaseCheck

EDGECONF_PATH = "/root/shared_v/edgeconf_pim.json"
# 채널 → edgeconf enable 키 (ch0/ch1 은 i2c2, ch2/ch3 은 i2c1 버스).
CHANNEL_ENABLE_KEYS = (
    ("ch0", ".VHL_CAM.i2c2.ch0.enable"),
    ("ch1", ".VHL_CAM.i2c2.ch1.enable"),
    ("ch2", ".VHL_CAM.i2c1.ch2.enable"),
    ("ch3", ".VHL_CAM.i2c1.ch3.enable"),
)
DEFAULT_SAMPLE_SEC = 3
DEFAULT_FPS_TOLERANCE_PCT = 10
TRANSIENT_TOKEN = "NEED_FRAME_FLOW"


def frame_flow_command(path: str, sample_sec: int) -> str:
    """스냅샷 2회(sample_sec 간격) + edgeconf [fps, ch0..ch3 enable] 을 한 왕복으로 읽는다.

    출력 3줄: "A=<json>", "B=<json>", "E=<jq 배열>". 파일이 없어도 빈 값으로 줄을
    남기고 마지막 echo 로 exit 0 을 지킨다 — exit≠0 이면 ssh.run 이 None 을 돌려줘
    진단이 사라진다.
    """
    jq_expr = "[" + ",".join([".VHL_CAM.fps"] + [key for _ch, key in CHANNEL_ENABLE_KEYS]) + "]"
    return (
        f"printf 'A='; cat {path} 2>/dev/null | tr -d '\\n'; echo; "
        f"sleep {sample_sec}; "
        f"printf 'B='; cat {path} 2>/dev/null | tr -d '\\n'; echo; "
        f"printf 'E='; jq -c '{jq_expr}' {EDGECONF_PATH} 2>/dev/null | tr -d '\\n'; echo"
    )


def _sample_sec(cfg: dict) -> int:
    value = cfg.get("sample_sec", DEFAULT_SAMPLE_SEC)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 1:
        return DEFAULT_SAMPLE_SEC
    return int(value)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _channels(snapshot: dict) -> dict:
    """{chN: {"status", "code", "counter"}} — gstreamer 블록의 채널 observation 만."""
    out: dict = {}
    observations = snapshot.get("observations")
    for obs in observations if isinstance(observations, list) else []:
        if not isinstance(obs, dict) or obs.get("block") != "gstreamer":
            continue
        scope = obs.get("scope")
        if not isinstance(scope, dict) or scope.get("kind") != "channel":
            continue
        counter = None
        for ev in obs.get("evidence") or []:
            if isinstance(ev, dict) and ev.get("name") == "enc_queue_input" and _is_number(ev.get("value")):
                counter = ev["value"]
        out[scope.get("id")] = {"status": obs.get("status"), "code": obs.get("code"),
                                "counter": counter}
    return out


class FrameFlowCheck(BaseCheck):
    name = "frame_flow"

    def collect(self, ssh, config: dict) -> dict:
        cfg = config.get("frame_flow") or {}
        if not cfg.get("enabled") or not cfg.get("path"):
            return {"skipped": True}
        return {"output": ssh.run(frame_flow_command(cfg["path"], _sample_sec(cfg)))}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if data.get("skipped"):
            return (True, "Skipped (not enabled)")
        cfg = config.get("frame_flow") or {}
        path = cfg.get("path")
        tol_pct = cfg.get("fps_tolerance_pct", DEFAULT_FPS_TOLERANCE_PCT)
        if not _is_number(tol_pct) or tol_pct < 0:
            tol_pct = DEFAULT_FPS_TOLERANCE_PCT

        output = data.get("output")
        if output is None:
            return (False, f"frame_flow collect returned no output — {TRANSIENT_TOKEN}")
        fields = {}
        for line in output.splitlines():
            key, sep, value = line.partition("=")
            if sep and key in ("A", "B", "E"):
                fields[key] = value.strip()

        reads = []
        for key in ("A", "B"):
            raw = fields.get(key)
            if not raw:
                return (False, f"{path} not present (producer not publishing yet) — {TRANSIENT_TOKEN}")
            try:
                snap = json.loads(raw)
            except ValueError:
                snap = None
            if not isinstance(snap, dict):
                return (False, f"{path} is not a JSON object — {TRANSIENT_TOKEN}")
            reads.append(snap)
        first, second = reads

        try:
            conf = json.loads(fields.get("E") or "")
        except ValueError:
            conf = None
        if (not isinstance(conf, list) or len(conf) != 1 + len(CHANNEL_ENABLE_KEYS)
                or not _is_number(conf[0]) or conf[0] <= 0
                or not all(isinstance(v, bool) for v in conf[1:])):
            return (False, f"edgeconf unreadable: [fps, ch0..ch3 enable] = {fields.get('E')!r}")
        exp_fps = conf[0]
        enables = conf[1:]

        # 두 읽기가 같은 producer 인스턴스여야 카운터를 비교할 수 있다.
        identity = []
        for key in ("pid", "boot_id"):
            if first.get(key) != second.get(key):
                identity.append(f"gstApp {key} changed {first.get(key)}→{second.get(key)} "
                                "between reads (restarted)")
        if identity:
            return (False, "; ".join(identity))

        seq_a, seq_b = first.get("sequence"), second.get("sequence")
        t_a, t_b = first.get("observed_monotonic_ms"), second.get("observed_monotonic_ms")
        if not (_is_number(seq_a) and _is_number(seq_b) and _is_number(t_a) and _is_number(t_b)
                and seq_b > seq_a and t_b > t_a):
            return (False, f"snapshot not advancing (sequence {seq_a}→{seq_b}, "
                           f"observed_monotonic_ms {t_a}→{t_b}) — {TRANSIENT_TOKEN}")
        dt = (t_b - t_a) / 1000.0

        chans_a, chans_b = _channels(first), _channels(second)
        hard: list[str] = []
        transient: list[str] = []
        ok: list[str] = []
        for (ch, _key), enabled in zip(CHANNEL_ENABLE_KEYS, enables):
            obs_b = chans_b.get(ch) or {}
            ca = (chans_a.get(ch) or {}).get("counter")
            cb = obs_b.get("counter")
            delta = cb - ca if ca is not None and cb is not None else None
            if enabled:
                if obs_b.get("code") == "DISABLED":
                    hard.append(f"{ch} enabled in edgeconf but gstApp reports DISABLED")
                elif delta is None:
                    transient.append(f"{ch} enc_queue_input not published yet "
                                     f"(status={obs_b.get('status')})")
                elif delta < 0:
                    hard.append(f"{ch} enc_queue_input went backwards {ca}→{cb}")
                else:
                    fps = delta / dt
                    window = f"{ca}→{cb} in {dt:.1f}s"
                    if delta == 0:
                        transient.append(f"{ch} enc_queue_input not advancing "
                                         f"(measured 0.0fps, expected {exp_fps}fps; {window})")
                    elif abs(fps - exp_fps) > exp_fps * tol_pct / 100.0:
                        hard.append(f"{ch} measured {fps:.1f}fps, expected {exp_fps}fps "
                                    f"±{tol_pct}% ({window})")
                    else:
                        ok.append(f"{ch} {fps:.1f}fps")
            else:
                if delta is not None and delta > 0:
                    hard.append(f"{ch} disabled in edgeconf but counting frames "
                                f"(+{delta} in {dt:.1f}s = {delta / dt:.1f}fps)")
                elif obs_b.get("code") != "DISABLED":
                    hard.append(f"{ch} disabled in edgeconf but gstApp reports "
                                f"{obs_b.get('status')}/{obs_b.get('code')} (expected DISABLED)")
                else:
                    ok.append(f"{ch} DISABLED")

        if hard:
            return (False, "; ".join(hard))
        if transient:
            return (False, "; ".join(transient) + f" — {TRANSIENT_TOKEN}")
        return (True, f"OK ({', '.join(ok)}; expected {exp_fps}fps ±{tol_pct}% over {dt:.1f}s)")
