from __future__ import annotations

"""Fail-closed hardware evidence primitives."""

from .rules import EvidenceError, Verdict, evaluate_rule

__all__ = ["EvidenceError", "Verdict", "evaluate_rule"]
