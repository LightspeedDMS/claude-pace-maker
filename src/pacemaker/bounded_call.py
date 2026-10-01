"""
Time-bounded calls for hooks with a hard timeout (bug #157, fix 4).

SubagentStart/SubagentStop have a 10 s budget. Their Langfuse step does network
I/O whose own timeouts (10 s for the trace push, 3 s for the OAuth profile) can
exceed it by themselves, and a hook the harness cancels delivers nothing. The
Langfuse step is therefore run through ``run_with_deadline``: the hook waits at
most the given budget and carries on with whatever finished.

Mechanism: the function runs in a DAEMON thread and the caller ``join``s it with
a timeout. Python threads cannot be killed, so on timeout the worker is
ABANDONED, not stopped -- it keeps running in the background until it finishes
or the hook process exits (a daemon thread never blocks interpreter exit). That
is acceptable for the callers here (an HTTP push and per-file state writes) and
is their documented trade-off: a bounded hook may leave a Langfuse trace
unpushed or unfinalized, never a subagent without its guidance.
"""

import threading
from typing import Any, Callable, Optional, Tuple


def run_with_deadline(
    fn: Callable[..., Any], timeout_seconds: float, *args: Any, **kwargs: Any
) -> Tuple[bool, Any]:
    """Run ``fn(*args, **kwargs)`` and wait at most ``timeout_seconds``.

    Returns:
        ``(True, result)`` when ``fn`` finished in time; ``(False, None)`` when
        the budget ran out (the worker is abandoned, see module docstring).

    Raises:
        Whatever ``fn`` raised, re-raised in the caller's thread, when it
        finished (with an exception) inside the budget.
    """
    outcome: dict = {}

    def worker() -> None:
        try:
            outcome["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # re-raised in the caller below
            outcome["error"] = exc

    thread = threading.Thread(target=worker, name="bounded-call", daemon=True)
    thread.start()
    thread.join(max(0.0, timeout_seconds))
    if thread.is_alive():
        return False, None
    error: Optional[BaseException] = outcome.get("error")
    if error is not None:
        raise error
    return True, outcome.get("result")
