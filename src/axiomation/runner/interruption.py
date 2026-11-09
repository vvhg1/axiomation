"""A person's interrupt (Ctrl-C) as a run's stop (docs/architecture.md
§ 3, "Stopping").

While a run asks (`held`), the first SIGINT raises nothing: it sets a
flag the process keeps, and each host's wait on its session
(`waited`) ends the session once it sees it. The runner and a queue
run then stop as they stop at the budget, and no later run in the
process asks anything. The handler then gives SIGINT back to the one
it replaced, so a second Ctrl-C raises `KeyboardInterrupt` at once.

A flag, not an exception: the interrupt reaches the main thread alone,
and the sessions run in worker threads, so the main thread goes on
waiting on them while they end and are recorded.
"""

from __future__ import annotations

import contextlib
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from enum import StrEnum
from types import FrameType
from typing import Any

#: How often a host's wait on its session looks whether the run was
#: interrupted.
POLL_SECONDS = 0.2

# A plain flag, not an `Event`: setting it takes no lock, so a second
# SIGINT within the handler cannot deadlock on one the first holds.
_interrupted = False
#: The SIGINT handler the outermost `held` replaced, given back by the
#: first interrupt.
_replaced: Callable[[int, FrameType | None], Any] | None = None


def interrupted() -> bool:
    """Whether a person interrupted a run in this process."""
    return _interrupted


def interrupt() -> None:
    """Interrupt the runs of this process, as a Ctrl-C does."""
    global _interrupted
    _interrupted = True


def clear() -> None:
    """Forget an interrupt: for a process that runs again after one."""
    global _interrupted
    _interrupted = False


def _on_interrupt(signum: int, frame: FrameType | None) -> None:
    interrupt()
    signal.signal(signal.SIGINT, _replaced or signal.default_int_handler)


@contextlib.contextmanager
def held() -> Iterator[None]:
    """A run's scope: a Ctrl-C within it interrupts the run (`interrupt`)
    instead of raising. Installed on the main thread alone, where Python
    takes a signal, and only over a handler of Python's: a process that
    ignores SIGINT goes on ignoring it. Within a scope already held, or
    after an interrupt, it leaves the handler as it is, so a second
    Ctrl-C still raises. The handler it replaced is back once the scope
    ends."""
    global _replaced
    previous = signal.getsignal(signal.SIGINT)
    if (
        threading.current_thread() is not threading.main_thread()
        or interrupted()
        or previous is _on_interrupt
        or not callable(previous)
    ):
        yield
        return
    _replaced = previous
    signal.signal(signal.SIGINT, _on_interrupt)
    try:
        yield
    finally:
        if signal.getsignal(signal.SIGINT) is _on_interrupt:
            signal.signal(signal.SIGINT, previous)
        _replaced = None


class Waited(StrEnum):
    """How a host's wait on its session's process ended."""

    EXITED = "exited"
    TIMED_OUT = "timed out"
    INTERRUPTED = "interrupted"


def waited(process: subprocess.Popen[bytes], timeout: float) -> Waited:
    """`process` waited for until it exits, `timeout` seconds pass, or the
    run is interrupted; a process that exits in the same poll as the
    interrupt has exited, so its answer is kept."""
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return Waited.TIMED_OUT
        try:
            process.wait(timeout=min(left, POLL_SECONDS))
            return Waited.EXITED
        except subprocess.TimeoutExpired:
            if interrupted():
                return Waited.INTERRUPTED


def cut_words(cut: int, floors: int, queue: bool = False) -> str:
    """The sessions an interrupt cut off, what is kept of them, and how
    many of them (`floors`) are counted at the least they spent, their
    turns priced; a queue's (`queue`) filed the tasks they settled. Empty
    where it cut none."""
    if not cut:
        return ""
    one = cut == 1
    words = f"{cut} session{'' if one else 's'} cut off" + (
        f": the tasks {'it' if one else 'they'} settled are filed, the one in "
        "hand is served again"
        if queue
        else " mid-answer, asked again from the start"
    )
    if floors:
        who = "it" if one else "they" if floors == cut else f"{floors} of them"
        words += (
            f"; what {who} spent is counted from {'its' if one else 'their'} "
            "turns, at least: the host says a session's whole cost only at its end"
        )
    return words


def stopped_words(cut: int = 0, floors: int = 0, queue: bool = False) -> str:
    """The interrupted stop's detail (`cut_words`), and that the same
    command resumes."""
    return (
        "; ".join(filter(None, ["by Ctrl-C", cut_words(cut, floors, queue)]))
        + ": the same command resumes"
    )
