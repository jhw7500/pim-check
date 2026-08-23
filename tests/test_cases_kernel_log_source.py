"""tests/test_cases_kernel_log_source.py — 커널 로그 체크의 소스·축·exit 규약 (pim-check#73).

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
        - `<분기> && echo A || echo B` — 두 분기 모두 출력하고 `echo` 는 exit 0.
          값을 **추출**하는 fsync 체크가 이 형태다(카운트가 아니라 fps 값을 비교).
          이 형태가 규약을 만족한다는 근거는 형태 목록이 아니라
          `TestFsyncCommandsActuallyHonorTheContract` 가 **실행으로** 댄다.
        """
        bad = []
        for fname, name, cmd, _ in _kernel_log_commands():
            ends_wc = cmd.rstrip().endswith("| wc -l")
            # "END 블록이 문자열에 있다" 가 아니라 "END 에 도달한다" 를 봐야 한다 —
            # awk 의 입력이 파이프여야 파일 열기 실패로 죽지 않는다.
            awk_reaches_end = ("END { print n+0 }" in cmd
                               and re.search(r"\|\s*awk\b", cmd) is not None)
            ends_echo_branches = re.search(
                r"&&\s*echo\s+\S+\s*\|\|\s*echo\s+\S+$", cmd.strip()) is not None
            if not (ends_wc or awk_reaches_end or ends_echo_branches):
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


def _all_custom_commands():
    """모든 케이스의 custom_commands 를 (파일명, 체크명, 명령, spec) 으로 산출."""
    out = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        prof = yaml.safe_load(path.read_text()) or {}
        if not isinstance(prof, dict):
            continue
        for chk in ((prof.get("checks") or {}).get("custom_commands") or []):
            out.append((path.name, chk.get("name"), chk.get("command", ""), chk))
    return out


def _fsync_commands():
    return [row for row in _all_custom_commands() if "max9296_fsync" in row[2]]


class TestNoCaseReadsTheRingBuffer:
    """`dmesg` 는 소스가 될 수 없다 — CLEAR 와 wrap 두 기제로 비워진다 (pim-check#69).

    ① `SYSLOG_ACTION_CLEAR`(`dmesg -C`)는 읽기 시작점만 옮긴다 — 보드 실측
       `dmesg -S` 5줄 vs `/dev/kmsg` 5,349줄.
    ② IMU 드라이버 폭주(`FIFO full data lost!` 2,182 레코드)로 인한 **진짜 wrap** —
       `/dev/kmsg` 의 가장 이른 레코드가 monotonic 37.98s 라 ~25s 의 fsync 마커가
       물리적으로 밀려났다.

    어느 쪽이든 링버퍼 기반 소스는 신뢰할 수 없다. `kern.log` 는 파일이라 둘 다 견딘다.
    """

    def test_no_custom_command_uses_dmesg(self):
        offenders = [f"{f}: {n}" for f, n, cmd, _ in _all_custom_commands()
                     if re.search(r"\bdmesg\b", cmd)]
        assert not offenders, (
            "dmesg 를 읽는 체크(kern.log 로 옮길 것):\n" + "\n".join(offenders))

    def test_fsync_checks_exist_and_read_kern_log(self):
        rows = _fsync_commands()
        # 코퍼스가 비면 위 가드가 조용히 무의미해진다 — 2026-08 기준 21건.
        assert len(rows) >= 20, f"fsync 체크가 너무 적다 ({len(rows)})"
        for fname, name, cmd, _ in rows:
            assert KERN_LOG in cmd, f"{fname}: {name}"


class TestFsyncDiagnosticsAreDistinguishable:
    """"소스가 없다" 와 "마커가 없다" 는 다른 사건이다 (pim-check#69 (c)).

    예전에는 둘 다 `FAIL:NO_DMESG` 한 가지로 보고돼, 보드에서 커널 로그가 통째로
    사라진 사고를 **케이스 결함으로 오인할 뻔했다.** 소스 교체와 무관하게 값이 있다.
    """

    def test_every_fsync_check_separates_source_from_marker(self):
        # #85 강등 후 게이팅하지 않으므로 FAIL: 접두 없이 토큰만 남는다 —
        # 구분 성질(두 사건이 다른 출력)은 그대로 유지된다.
        for fname, name, cmd, _ in _fsync_commands():
            assert "NO_SOURCE" in cmd, f"{fname}: {name} — 소스 부재 진단 없음"
            assert "NO_MARKER" in cmd, f"{fname}: {name} — 마커 부재 진단 없음"

    def test_old_undifferentiated_diagnosis_is_gone(self):
        offenders = [f"{f}: {n}" for f, n, cmd, _ in _all_custom_commands()
                     if "NO_DMESG" in cmd]
        assert not offenders, (
            "NO_DMESG 는 두 사건을 뭉뚱그린다:\n" + "\n".join(offenders))


class TestFsyncIsDiagnosticOnly:
    """#85 A — 커널 로그 텍스트가 케이스 통과/실패를 결정하는 자리에서 빠졌다.

    fsync 마커는 SERDES/ISP 가 **내보낸** 레이트이고 ffprobe 는 videorate·mux 를
    거쳐 **기록된** 레이트라, 둘의 어긋남 자체가 병목 진단이다 — 그래서 지우지
    않고 강등한다. 게이팅 fps 는 같은 케이스의 ffprobe 실측이 담당한다.
    대체 없이 빼면 커버리지 손실이므로, 강등된 파일마다 실측이 있는지도 본다.
    """

    def test_fsync_checks_do_not_gate(self):
        offenders = [f"{f}: {n}" for f, n, _, spec in _fsync_commands()
                     if "expected" in spec or "expected_min" in spec]
        assert not offenders, (
            "fsync 마커가 다시 게이팅한다(#85 회귀):\n" + "\n".join(offenders))

    def test_gating_fps_measurement_exists_wherever_fsync_is_diagnostic(self):
        by_file: dict[str, list] = {}
        for f, n, cmd, spec in _all_custom_commands():
            by_file.setdefault(f, []).append((n, cmd, spec))
        missing = []
        for f, _n, _cmd, _spec in _fsync_commands():
            # avg_frame_rate 를 본다 — r_frame_rate 는 타임스탬프 기저 레이트라
            # 프레임 드랍에도 설정값(30/1)을 유지할 수 있다(#102 Codex P1).
            gating_fps = [nn for nn, cc, ss in by_file[f]
                          if "avg_frame_rate" in cc and ss.get("expected") is not None]
            if not gating_fps:
                missing.append(f)
        assert not missing, (
            "fsync 를 강등했는데 게이팅 fps 실측이 없는 파일(커버리지 손실):\n"
            + "\n".join(sorted(set(missing))))

    def test_multi_integrity_fps_matches_case_edgeconf(self):
        """무결성 fps 단언의 기대값은 그 케이스의 edgeconf fps 와 같아야 한다."""
        checked = 0
        for path in sorted(CASES_DIR.glob("multi_*.yaml")):
            prof = yaml.safe_load(path.read_text())
            exp = prof["setup"]["edgeconf_changes"][".VHL_CAM.fps"]
            for chk in prof["checks"]["custom_commands"]:
                if "파일 무결성" not in (chk.get("name") or ""):
                    continue
                cmd = chk["command"]
                assert "avg_frame_rate" in cmd, f"{path.name}: {chk['name']}"
                assert "r_frame_rate" not in cmd, (
                    f"{path.name}: {chk['name']} — r_frame_rate 는 드랍을 못 본다")
                m = re.search(r"n>=(\d+)-0\.5", cmd)
                assert m and int(m.group(1)) == exp, (
                    f"{path.name}: {chk['name']} — 기대 fps "
                    f"{m and m.group(1)} != edgeconf {exp}")
                checked += 1
        assert checked == 36, f"무결성 fps 단언이 {checked}건 (기대 36)"


class TestFsyncCommandsActuallyHonorTheContract:
    """fsync 체크 21건을 **실제 셸에서 돌려** exit 규약을 확인한다.

    형태 목록에 항목을 하나 더 추가하는 것으로 끝내면, 그 형태가 정말 규약을
    만족하는지는 아무도 확인하지 않은 채로 남는다 — 이 저장소에서 형태 기반 단언이
    위반을 통과시킨 전례가 여러 건 있다. 세 경우(소스 없음 / 마커 없음 / 마커 있음)를
    직접 만들어 **exit 0 이고 출력이 비지 않는지** 본다.
    """

    def _run(self, cmd: str, source: str | None):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            if source is None:
                path = str(pathlib.Path(d) / "absent.log")
            else:
                path = str(pathlib.Path(d) / "kern.log")
                pathlib.Path(path).write_text(source)
            # 앵커 파일도 이 임시 디렉터리로 돌려 호스트 상태에 의존하지 않게 한다.
            run_cmd = (cmd.replace(KERN_LOG, path)
                          .replace("/tmp/pim_check_anchor", str(pathlib.Path(d) / "anchor")))
            return subprocess.run(["sh", "-c", run_cmd], capture_output=True, text=True)

    # 앵커가 없으면 `uptime -s`(부팅 시각)로 폴백하므로, 마커 줄은 그보다 뒤여야 한다.
    _MARKER = ("2099-01-01 00:00:00.219 kernel[notice][   25.557314] "
               "[I2C:1][max9296.c:4612] max9296_fsync side fps : {fps}, low : 32333\n")

    def _fsync_rows(self):
        rows = [r for r in _kernel_log_commands() if "max9296_fsync" in r[2]]
        assert len(rows) >= 20, f"fsync 체크가 너무 적다 ({len(rows)})"
        return rows

    def test_missing_source_exits_zero_with_output(self):
        for fname, name, cmd, _ in self._fsync_rows():
            r = self._run(cmd, None)
            assert r.returncode == 0, f"{fname}: {name} — exit {r.returncode}"
            assert r.stdout.strip(), f"{fname}: {name} — 무출력 (ssh.run 이 None 을 받는다)"
            assert "NO_SOURCE" in r.stdout, f"{fname}: {name} — {r.stdout!r}"

    def test_source_without_marker_exits_zero_with_output(self):
        for fname, name, cmd, _ in self._fsync_rows():
            r = self._run(cmd, "2099-01-01 00:00:00.000 kernel[notice][ 1.0] nothing here\n")
            assert r.returncode == 0, f"{fname}: {name} — exit {r.returncode}"
            assert "NO_MARKER" in r.stdout, f"{fname}: {name} — {r.stdout!r}"

    def test_marker_reports_the_observed_value(self):
        """#85 강등 후 이 체크는 관측값 보고가 전부다 — 기대 비교를 하지 않는다
        (게이팅은 같은 케이스의 ffprobe fps 실측이 담당)."""
        for fname, name, cmd, _ in self._fsync_rows():
            r = self._run(cmd, self._MARKER.format(fps=42))
            assert r.returncode == 0, f"{fname}: {name} — exit {r.returncode}"
            assert r.stdout.strip() == "got=42", f"{fname}: {name} — {r.stdout!r}"

    def test_divergent_markers_are_all_reported(self):
        """SERDES 가 두 레이트를 오갔으면 그 자체가 진단 — 값 전부가 보여야 한다."""
        fname, name, cmd, _ = self._fsync_rows()[0]
        src = self._MARKER.format(fps=15) + self._MARKER.format(fps=16)
        r = self._run(cmd, src)
        assert r.returncode == 0
        assert r.stdout.strip() == "got=15,16", f"{fname}: {name} — {r.stdout!r}"

    def test_previous_boot_marker_is_not_an_observation(self):
        """kern.log 는 재부팅을 넘어 산다 — 과거 부팅 값이 관측값으로 잡히면 안 된다."""
        fname, name, cmd, _ = self._fsync_rows()[0]
        stale = ("1999-01-01 00:00:00.000 kernel[notice][   25.557314] "
                 "[I2C:1][max9296.c:4612] max9296_fsync side fps : 15\n")
        r = self._run(cmd, stale)
        assert r.returncode == 0
        assert "NO_MARKER" in r.stdout, (
            f"{fname}: {name} — 과거 부팅 줄이 관측됐다: {r.stdout!r}")


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


class TestTimestampFormatCoupling:
    """`substr($0,1,19) > bt` 비교는 **세 생산자가 같은 형식**이라는 데 의존한다.

    - rsyslog 템플릿 → kern.log 줄머리 `YYYY-MM-DD HH:MM:SS` (19자)
    - `setup._write_session_anchor` → 앵커 1행에 `uptime -s` 기록 (같은 19자)
    - 케이스의 `uptime -s` 폴백 (같은 19자)

    영(0)패딩 ISO 형식이라 **사전식 비교 = 시간 순서 비교**가 성립한다. 우연이 아니라
    형식의 성질이다.

    진짜 위험은 비교 의미가 아니라 **결합이 세 곳에 흩어져 있고 서로를 모른다**는
    것이다. 하나만 바뀌어도 비교는 에러 없이 **조용히 틀린다**. 특히 이 저장소는
    "monotonic > wall-clock" 을 여러 번 채택했는데(#69·#71·#73), 그 논리를 나중에
    세션 앵커에 적용하면 1행이 숫자가 되고 그 순간 이 체크들과 케이스 38곳이
    **에러 없이 오스코핑**된다. 그래서 형식을 여기서 못박는다 — 바뀌면 조용한
    오작동 대신 **테스트 실패**로 나타난다.
    """

    TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_anchor_writer_records_wall_clock_first_line(self):
        """`_write_session_anchor` 1행이 `uptime -s`(19자 wall-clock)여야 한다.

        **메서드가 실제로 만드는 명령을 캡처해서** 본다. 소스 텍스트를 grep 하면
        docstring·주석이 오염원이 된다 — 누가 앵커를 monotonic 으로 바꾸면서
        "이전에는 `$(uptime -s)` 를 썼다" 를 주석에 적기만 해도 통과한다.
        (이 테스트를 쓰면서 실제로 그 누출을 겪었고, 문자열을 좁히는 방식으로는
        한 칸 물러날 뿐이라 **텍스트 검사에서 동작 검사로 범주를 바꿨다.**)
        런타임 문자열에는 주석이 없고, 리포맷에도 견디며, 소스 오프셋 매직값도 없다.
        """
        from unittest.mock import MagicMock

        from setup import SetupManager

        mgr = SetupManager(MagicMock())
        mgr.ssh.run.return_value = "2026-08-22 02:08:22\n0f1e2d3c"
        mgr._write_session_anchor()
        cmd = mgr.ssh.run.call_args[0][0]
        assert "$(uptime -s)" in cmd, (
            "앵커 1행이 더 이상 wall-clock($(uptime -s))이 아니다 — "
            "substr($0,1,19) 비교를 쓰는 체크 9건과 케이스 38곳이 "
            "에러 없이 조용히 오스코핑된다")

    def test_sample_timestamps_are_19_chars(self):
        """kern.log 줄머리와 `uptime -s` 가 같은 19자 형식임을 고정."""
        kern_line = ("2026-08-22 02:08:47.219 kernel[notice][   25.557314] "
                     "[I2C:2][max9296.c:1197] ch0 MCP4018(0x2f) write fail")
        uptime_s = "2026-08-22 02:08:22"
        assert self.TS_RE.match(kern_line[:19]), kern_line[:19]
        assert self.TS_RE.match(uptime_s), uptime_s
        assert len(uptime_s) == 19
        # 영패딩 ISO → 사전식 비교가 시간 순서와 일치한다.
        assert kern_line[:19] > uptime_s

    def test_checks_expect_that_exact_width(self):
        """wall-clock 축을 쓰는 체크는 `substr($0,1,19)` 로 그 폭을 기대한다."""
        wall_clock_checks = [(f, n) for f, n, cmd, _ in _kernel_log_commands()
                             if "pim_check_anchor" in cmd]
        assert wall_clock_checks, "wall-clock 축 체크가 없다 — 코퍼스 확인"
        for fname, name, cmd, _ in _kernel_log_commands():
            if "pim_check_anchor" not in cmd:
                continue
            assert "substr($0,1,19)" in cmd, f"{fname}: {name}"
