from __future__ import annotations
from abc import ABC, abstractmethod

from event_stream import serialize_check_pass_event, serialize_fail_event, serialize_pending_event
from verify_retry import is_stabilization_reason


class BaseCheck(ABC):
    name: str = "unnamed"
    scope: str = "snapshot"

    @abstractmethod
    def collect(self, ssh, config: dict) -> dict:
        """Collect data from target via SSH. Returns raw data dict."""

    @abstractmethod
    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        """Validate collected data. Returns (passed, reason)."""

    def validate_and_emit(self, data: dict, config: dict, emitter=None, **context) -> tuple[bool, str]:
        """Run validate() and emit exactly one event on the Fail path.

        On a Fail outcome the emitted event is a ``pending`` event when the reason
        is a stabilization/not-ready signal (NEED_2_FINALIZES 등), otherwise a
        ``fail`` event. This keeps "still warming up" out of the fault stream.

        Wires the JSONL fail emitter into the validate() path so that real-time
        fault visibility (PimEventStream ``fail`` event) is produced the moment a
        check fails — without altering the (passed, reason) contract of
        ``validate()`` or touching the existing *_results.json batch output.

        Behaviour:
            * Calls ``self.validate(data, config)`` exactly once.
            * If the result is a Fail (``passed`` is falsy) AND an ``emitter`` is
              provided, invokes ``emitter`` exactly once with a single-line JSONL
              fail event built by :func:`serialize_fail_event` (carrying this
              check's ``name``, the validate ``reason``, and any extra
              ``context`` fields such as run_id/plan/board/case_name).
            * On a Pass outcome the emitter is never called.

        Args:
            data: Raw data dict from ``collect()``.
            config: Check configuration dict.
            emitter: Optional callable taking the serialized fail-event line.
                When ``None`` no event is emitted (back-compatible default).
            **context: Extra fail-event fields (None-valued fields are dropped
                by ``serialize_fail_event``).

        Returns:
            The unchanged ``(passed, reason)`` tuple from ``validate()``.
        """
        passed, reason = self.validate(data, config)
        if emitter is not None:
            if not passed:
                # 안정화 미달(NEED_2_FINALIZES 등)은 장애가 아니라 '준비 중' → pending,
                # 그 외 실제 결함만 fail 로 표면화한다.
                if is_stabilization_reason(reason):
                    emitter(serialize_pending_event(self.name, reason, **context))
                else:
                    emitter(serialize_fail_event(self.name, reason, **context))
            else:
                # 통과 신호 — 뷰어가 케이스 실행 중에도 이 항목을 ✓ 로 표시할 수 있도록.
                emitter(serialize_check_pass_event(self.name, **context))
        return passed, reason
