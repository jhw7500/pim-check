from __future__ import annotations

from checks.base_check import BaseCheck


class ProcessCheck(BaseCheck):
    name = "process"

    def collect(self, ssh, config: dict) -> dict:
        """타겟에서 프로세스 실행 여부 및 CPU 사용률을 수집한다."""
        required = config["processes"]["required"]
        optional = config["processes"]["optional"]
        all_procs = required + optional

        running = []
        missing = []
        cpu: dict[str, float] = {}

        for proc in all_procs:
            result = ssh.run(f"pgrep -x {proc}")
            if result is not None:
                running.append(proc)
                cpu_raw = ssh.run(f"ps -C {proc} -o %cpu= | head -1")
                try:
                    cpu[proc] = float(cpu_raw)
                except (TypeError, ValueError):
                    cpu[proc] = 0.0
            else:
                missing.append(proc)

        return {"running": running, "missing": missing, "cpu": cpu}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        """수집된 데이터를 기준값과 비교하여 통과 여부를 반환한다."""
        required = config["processes"]["required"]
        bg_check_max = config["cpu"]["bg_check_max_pct"]
        gst_min, gst_max = config["cpu"]["gst_range"]

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
