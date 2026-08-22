"""tests/test_kernel_log_source.py — 커널 로그 체크의 소스·축·exit 규약 (pim-check#73).

`journalctl -k` 를 읽던 fault 체크 11건은 **구조적으로 실패할 수 없었다**:
소스가 사실상 비어 있고(보드 실측: journalctl -k 31줄 vs kern.log 56,140줄)
`expected: "0"` 이라, 결함 발생 여부와 무관하게 PASS 했다. 증명적으로 무효인 것이
3건 있었다(`fault_cam_disconnect`·`fault_i2c_bus_error`·`board_error_detect` — 각각
kern.log 에는 540/540/3978 건이 매칭되는데 journalctl 에서는 0).

소스를 rsyslog 의 `/var/log/cantops/kern.log` 로 옮기고 스코핑 축을 명시했다.
이 파일은 그 상태가 되돌아가지 않도록 고정한다.
"""
from __future__ import annotations

import pathlib
import re

import yaml

CASES_DIR = pathlib.Path(__file__).resolve().parent.parent / "profiles" / "cases"
KERN_LOG = "/var/log/cantops/kern.log"


def _kernel_log_commands():
    """커널 로그를 읽는 custom_commands 를 (파일명, 체크명, 명령, spec) 으로 산출."""
    out = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        prof = yaml.safe_load(path.read_text()) or {}
        if not isinstance(prof, dict):
            continue
        for chk in ((prof.get("checks") or {}).get("custom_commands") or []):
            cmd = chk.get("command", "")
            if KERN_LOG in cmd or "journalctl -k" in cmd:
                out.append((path.name, chk.get("name"), cmd, chk))
    return out


class TestKernelLogSource:
    def test_no_case_reads_journalctl_k(self):
        """`journalctl -k` 로 되돌아가면 안 된다 — 이 보드에서 그 싱크는 사실상 비어 있다.

        journald 는 max9296 커널 로그를 전 부팅 통틀어 0건 담았다(보드 실측).
        `expected: "0"` 과 결합하면 결함이 나도 통과하는 거짓 PASS 가 된다.
        """
        offenders = [f"{f}: {n}" for f, n, cmd, _ in _kernel_log_commands()
                     if "journalctl -k" in cmd]
        assert not offenders, "journalctl -k 로 되돌아간 체크:\n" + "\n".join(offenders)

    def test_all_kernel_log_checks_use_kern_log(self):
        cmds = _kernel_log_commands()
        # 코퍼스가 비면 테스트가 조용히 무의미해진다 — 2026-08 기준 11건.
        assert len(cmds) >= 10, f"커널 로그 체크가 너무 적다 ({len(cmds)})"
        for fname, name, cmd, _ in cmds:
            assert KERN_LOG in cmd, f"{fname}: {name}"

    def test_reads_binary_safe(self):
        """kern.log 는 바이너리로 판정될 수 있다 — `grep -a` 없으면 'Binary file matches' 로
        줄이 아니라 한 문장이 나와 카운트가 망가진다(보드 실측)."""
        for fname, name, cmd, _ in _kernel_log_commands():
            if "grep" not in cmd:
                continue  # awk 단독 형태(monotonic)는 해당 없음
            # 플래그에 대문자가 섞인다(-aiE, -avE) — 문자 클래스에 포함해야 한다.
            assert re.search(r"grep -[a-zA-Z]*a[a-zA-Z]* ", cmd), f"{fname}: {name} — grep -a 없음"


class TestScopingAxis:
    """스코핑 축 — kern.log 는 재부팅을 넘어 살아남으므로 필터가 유일한 스코핑이다."""

    def test_every_check_is_scoped(self):
        """전체 파일을 훑는 체크가 없어야 한다 — 4월치까지 보존되므로 과거 부팅이 섞인다."""
        unscoped = []
        for fname, name, cmd, _ in _kernel_log_commands():
            has_anchor = "pim_check_anchor" in cmd and "substr($0,1,19) > bt" in cmd
            has_monotonic = "if (t<prev) n=0" in cmd
            if not (has_anchor or has_monotonic):
                unscoped.append(f"{fname}: {name}")
        assert not unscoped, "스코핑 없이 kern.log 전체를 훑는 체크:\n" + "\n".join(unscoped)

    def test_rtc_check_uses_monotonic_not_wall_clock(self):
        """`fault_rtc_fail` 만은 시계를 쓰면 안 된다.

        이 체크의 **가설이 "RTC 통신 실패"** 다. RTC 가 고장 난 맥락에서는 시스템 시계를
        신뢰할 수 없고(부팅 시 RTC 에서 시각을 읽으므로), wall-clock 필터가 엉뚱한 구간을
        가리킨다 — 자신이 검출하려는 결함에 의해 자신의 필터가 망가지는 구조다.
        monotonic 은 시계와 무관하고 재부팅마다 0 으로 리셋돼 부팅 스코핑도 된다.
        """
        prof = yaml.safe_load((CASES_DIR / "fault_rtc_fail.yaml").read_text())
        cmds = [c["command"] for c in prof["checks"]["custom_commands"]
                if KERN_LOG in c.get("command", "")]
        assert len(cmds) == 1
        cmd = cmds[0]
        assert "if (t<prev) n=0" in cmd, "monotonic 부팅경계 검출이 없다"
        assert "uptime -s" not in cmd, "wall-clock 을 쓰고 있다"
        assert "pim_check_anchor" not in cmd, "wall-clock 앵커를 쓰고 있다"


class TestExitConvention:
    """커널 로그 명령은 **항상 exit 0 이고 항상 출력이 있어야 한다**.

    `ssh.run` 은 exit≠0 에 `None` 을 반환하므로, 출력이 없으면 체크가 조용히
    깨진다. 충족 형태는 `… | wc -l`(파이프라인 종료코드 0) 또는
    `awk … END{print}`(무조건 출력) 다.
    """

    def test_every_command_always_exits_zero_with_output(self):
        """충족 형태를 **닫힌 목록**으로 두고, 각각이 왜 성질을 만족하는지 근거를 단다.

        - `… | wc -l` — `wc` 가 항상 exit 0 · 항상 출력
        - `<파이프> | awk … END{print}` — **입력이 비어도** END 에 도달
        - `awk … <파일>` — **불충족**: 파일을 못 열면 awk 가 fatal 로 죽어
          **END 에 도달하지 못한다**(exit 2 + 무출력 → `ssh.run` 이 None).
          보드 실측으로 재현했다. 로테이션 순간·부팅 초기·마운트 실패에서 발현한다.
        """
        bad = []
        for fname, name, cmd, _ in _kernel_log_commands():
            ends_wc = cmd.rstrip().endswith("| wc -l")
            # "END 블록이 문자열에 있다" 가 아니라 "END 에 도달한다" 를 봐야 한다 —
            # awk 의 입력이 파이프여야 파일 열기 실패로 죽지 않는다.
            awk_reaches_end = ("END { print n+0 }" in cmd
                               and re.search(r"\|\s*awk\b", cmd) is not None)
            if not (ends_wc or awk_reaches_end):
                bad.append(f"{fname}: {name}")
        assert not bad, "exit 규약을 충족하지 않는 명령:\n" + "\n".join(bad)

    def test_awk_never_reads_the_file_directly(self):
        """`awk … <파일>` 금지 — 파일 열기 실패 시 END 미도달로 규약이 깨진다.

        실증(보드·로컬 동일):
            awk 'END{print n+0}' /nonexistent  → exit 2, 무출력
            cat /nonexistent 2>/dev/null | awk 'END{print n+0}' → "0", exit 0
        """
        offenders = []
        for fname, name, cmd, _ in _kernel_log_commands():
            if "awk" not in cmd:
                continue
            # 파일이 `awk` **뒤에** 나오면 awk 의 인자다(= 위반).
            # 파이프/grep 형태는 파일이 awk 앞에 있으므로 걸리지 않는다.
            # (정규식으로 범위를 좁히려다 두 번 틀렸다 — awk 정규식 안의 `|` 에
            #  걸려 못 잡거나, 반대로 grep 인자까지 잡았다. 위치 비교가 정확하다.)
            # **마지막** awk 기준 — 9건은 앵커를 읽는 awk 가 앞에 하나 더 있는데,
            # 그건 `$( )` 안이라 실패해도 흡수되고 최종 출력을 내는 것은 마지막 awk 다.
            # (첫 awk 기준으로 잡으면 그 앵커 리더 때문에 전부 오탐한다 — 실제로 그랬다.)
            idx = cmd.rfind("awk")
            if idx >= 0 and KERN_LOG in cmd[idx:]:
                offenders.append(f"{fname}: {name}")
        assert not offenders, (
            "awk 가 파일을 직접 읽는다(파이프로 바꿀 것):\n" + "\n".join(offenders))


class TestExpectationsAreNotVacuous:
    def test_no_vacuous_expected_min_zero(self):
        """`expected_min: 0` 은 어떤 값이든 만족해 단언이 아니다.

        `fault_sd_unmounted` 가 그랬다 — `on_fail` 은 "감지 안 됨" 인데 임계가 0 이라
        아무것도 감지 못해도 통과했다(#73).
        """
        vacuous = [f"{f}: {n}" for f, n, _, spec in _kernel_log_commands()
                   if spec.get("expected_min") == 0]
        assert not vacuous, "expected_min: 0 (항상 참):\n" + "\n".join(vacuous)


class TestBspExclusionDoesNotSwallowRealErrors:
    """`board_error_detect` 의 BSP 제외 필터가 **진짜 오류를 삼키지 않아야** 한다.

    하드코딩 목록의 진짜 위험은 유지보수성이 아니라 **침식**이다 — 다음 사람이
    시끄러운 드라이버를 하나씩 덧붙이다 보면 필터가 조용히 넓어져 실제 오류까지
    가린다. 보드에서 1회 확인한 것(진짜 오류 540건이 제외를 통과)을 여기서 영구
    가드로 박는다.
    """

    # 보드 실측 라인 (2026-08-22)
    REAL_ERRORS = [
        "2026-08-22 02:08:56.5 kernel[err][   26.503147] [I2C:2][max9296.c:1197] "
        "ch0 MCP4018(0x2f) write fail: wiper=0x10",
        "2026-08-22 02:08:56.5 kernel[err][   26.503514] [I2C:2][max9296.c:2679] "
        "ch0 dual applied fail (ret=-6)",
        "2026-08-19 08:23:33.090 kernel[err][10414.611286] [I2C:2][max9296.c:1001] "
        "ch0 Error i2c write reg : [0x40] reg=0x3f1(2 byte), val=0x85(1 byte)",
    ]
    BSP_NOISE = [
        "2026-08-22 02:08:22.1 kernel[warning][    2.185197] imx-micfil: "
        "probe of sound-micfil failed with error -22",
        "2026-08-22 02:08:22.1 kernel[err][    0.201338] imx8-pcie-phy 32f00000.pcie-phy: "
        "failed to get imx pcie phy clock",
        "2026-08-22 02:08:22.1 kernel[warning][    1.452057] spi-nor: "
        "probe of spi0.0 failed with error -2",
        "2026-08-22 02:08:22.1 kernel[err][   11.599224] ieee80211 phy0: "
        "lrdmwl_pcie: pci_enable_msi failed -22",
    ]

    @staticmethod
    def _exclusion_pattern():
        prof = yaml.safe_load((CASES_DIR / "board_error_detect.yaml").read_text())
        cmd = next(c["command"] for c in prof["checks"]["custom_commands"]
                   if KERN_LOG in c.get("command", ""))
        m = re.search(r"grep -avE '([^']+)'", cmd)
        assert m, "제외 필터를 찾을 수 없다"
        return m.group(1)

    def test_real_errors_survive_the_filter(self):
        excl = re.compile(self._exclusion_pattern())
        swallowed = [ln for ln in self.REAL_ERRORS if excl.search(ln)]
        assert not swallowed, (
            "제외 필터가 진짜 오류를 삼킨다:\n" + "\n".join(swallowed))

    def test_bsp_noise_is_excluded(self):
        excl = re.compile(self._exclusion_pattern())
        leaked = [ln for ln in self.BSP_NOISE if not excl.search(ln)]
        assert not leaked, (
            "제외돼야 할 BSP 잡음이 남는다:\n" + "\n".join(leaked))
