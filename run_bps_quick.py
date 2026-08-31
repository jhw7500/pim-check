#!/usr/bin/env python3
from __future__ import annotations

"""Compatibility entry point for the centrally evaluated BPS hardware gate."""

import json
from pathlib import Path

from hw_gate.adapters.bps import run_local_bps


RESULT = Path(__file__).resolve().parent / "bps_quick_results.json"


def main() -> int:
    try:
        result = run_local_bps(RESULT)
    except Exception as exc:
        result = {
            "verdict": "ERROR",
            "errors": [{"code": "bps.local_runner_error", "message": str(exc)}],
        }
    RESULT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    restoration = result.get("restoration", {})
    return 0 if (
        result.get("verdict") == "PASS"
        and isinstance(restoration, dict)
        and restoration.get("verdict") == "PASS"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
