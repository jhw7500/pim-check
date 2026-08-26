#!/usr/bin/env python3
from __future__ import annotations

"""Compatibility entry point for the centrally evaluated mixed-combo gate."""

import json
from pathlib import Path

from hw_gate.adapters.mixed_combo import run_local_mixed_combo


RESULT = Path(__file__).resolve().parent / "mixed_combo_results.json"


def main() -> int:
    try:
        result = run_local_mixed_combo(RESULT)
    except Exception as exc:
        result = {
            "verdict": "ERROR",
            "errors": [{"code": "mixed_combo.local_runner_error", "message": str(exc)}],
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
