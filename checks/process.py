from __future__ import annotations

from checks.base_check import BaseCheck


class ProcessCheck(BaseCheck):
    name = "process"

    def collect(self, ssh, config: dict) -> dict:
        """타겟에서 프로세스 실행 여부 및 CPU 사용률을 수집한다."""
        proc_config = config.get("processes", {})
        required = proc_config.get("required", [])
        optional = proc_config.get("optional", [])
        all_procs = required + optional

        running = []
        missing = []
        cpu: dict[str, float] = {}

        for proc in all_procs:
            # pgrep -x: 정확한 프로세스 이름 매칭 (바이너리)
            # pgrep -f: 명령줄 전체 매칭 (셸 스크립트 폴백)
            result = ssh.run(f"pgrep -x {proc}")
            if not result:
                result = ssh.run(f"pgrep -f {proc}")
            # 빈 문자열("")도 '미기동'으로 취급. pgrep 은 매치 없으면 exit!=0 + 빈 stdout 을
            # 내는데, ssh.run 이 "" 를 돌려주면 'result is not None' 으로는 안 걸러져
            # splitlines()[0] 가 IndexError 를 냈다(리부트 직후 프로세스 미기동 윈도우).
            # truthiness 로 None/"" 를 함께 처리한다.
            lines = result.splitlines() if result else []
            if lines:
                running.append(proc)
                # ps -C는 정확한 이름만 지원하므로 PID 기반으로 CPU 조회
                pid = lines[0].strip()
                cpu_raw = ssh.run(f"ps -p {pid} -o %cpu= 2>/dev/null | head -1")
                try:
                    cpu[proc] = float(cpu_raw)
                except (TypeError, ValueError):
                    cpu[proc] = 0.0
            else:
                missing.append(proc)

        return {"running": running, "missing": missing, "cpu": cpu}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        """수집된 데이터를 기준값과 비교하여 통과 여부를 반환한다."""
        required = config.get("processes", {}).get("required", [])
        cpu_config = config.get("cpu", {})
        bg_check_max = cpu_config.get("bg_check_max_pct", 3.0)
        gst_range = cpu_config.get("gst_range", [0, 100])
        gst_min = gst_range[0] if len(gst_range) >= 1 else 0
        gst_max = gst_range[1] if len(gst_range) >= 2 else 100

        issues = []

        # 필수 프로세스 누락 확인
        for proc in required:
            if proc in data["missing"]:
                issues.append(f"{proc} is not running")

        # BG_Check_for_pim CPU 상한 확인
        bg_cpu = data["cpu"].get("BG_Check_for_pim")
        if bg_cpu is not None and bg_cpu > bg_check_max:
            issues.append(
                f"BG_Check_for_pim CPU {bg_cpu}% > max {bg_check_max}%"
            )

        # gstApp CPU 범위 확인
        gst_cpu = data["cpu"].get("gstApp")
        if gst_cpu is not None and not (gst_min <= gst_cpu <= gst_max):
            issues.append(
                f"gstApp CPU {gst_cpu}% out of range [{gst_min}, {gst_max}]"
            )

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")
