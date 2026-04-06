"""
color.py — 터미널 컬러 유틸리티

Windows CMD에서도 동작하도록 ANSI 지원 여부를 자동 감지.
"""
from __future__ import annotations

import os
import sys


def _supports_color() -> bool:
    """터미널이 ANSI 컬러를 지원하는지 확인."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(sys.stdout, "isatty"):
        return False
    if not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        # Windows 10+ ANSI 지원
        try:
            os.system("")  # ANSI 활성화
            return True
        except Exception:
            return False
    return True


_COLOR = _supports_color()


def green(text: str) -> str:
    return f"\033[32m{text}\033[0m" if _COLOR else text


def red(text: str) -> str:
    return f"\033[31m{text}\033[0m" if _COLOR else text


def yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m" if _COLOR else text


def bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if _COLOR else text


def dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if _COLOR else text
