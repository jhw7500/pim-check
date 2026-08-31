from __future__ import annotations

import contextlib
import signal
from typing import Iterator


class TerminationRequested(BaseException):
    """Raised by the two catchable termination signal handlers."""

    def __init__(self, signum: int) -> None:
        super().__init__("termination signal {0}".format(signum))
        self.signum = signum


@contextlib.contextmanager
def installed_termination_handlers() -> Iterator[None]:
    """Turn catchable process termination into transaction-unwinding exceptions."""
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}

    def terminate(signum: int, frame: object) -> None:
        del frame
        raise TerminationRequested(signum)

    try:
        for signum in previous:
            signal.signal(signum, terminate)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
