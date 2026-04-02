"""
checks/thermal.py - CPU/SoC 온도 체크
"""
from __future__ import annotations

from checks.base_check import BaseCheck

THERMAL_ZONE_PATH = "/sys/devices/virtual/thermal/thermal_zone{}/temp"


class ThermalCheck(BaseCheck):
    name = "thermal"

    def collect(self, ssh, config: dict) -> dict:
        temps: dict[int, float] = {}
        for zone in range(2):
            result = ssh.run(f"cat {THERMAL_ZONE_PATH.format(zone)}")
            if result is not None:
                temps[zone] = int(result) / 1000.0
        max_temp = max(temps.values()) if temps else 0.0
        return {"temps": temps, "max_temp": max_temp}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        thermal_cfg = config.get("thermal", {})
        max_allowed = thermal_cfg.get("max_temp_c", 85)
        warn_temp = thermal_cfg.get("warn_temp_c", 80)
        max_temp = data["max_temp"]

        if max_temp > max_allowed:
            return (False, f"Temperature {max_temp} > max {max_allowed}")
        if max_temp > warn_temp:
            return (True, f"WARN: Temperature {max_temp} > warn {warn_temp}")
        return (True, "OK")
