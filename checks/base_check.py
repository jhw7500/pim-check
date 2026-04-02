from __future__ import annotations
from abc import ABC, abstractmethod


class BaseCheck(ABC):
    name: str = "unnamed"

    @abstractmethod
    def collect(self, ssh, config: dict) -> dict:
        """Collect data from target via SSH. Returns raw data dict."""

    @abstractmethod
    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        """Validate collected data. Returns (passed, reason)."""
