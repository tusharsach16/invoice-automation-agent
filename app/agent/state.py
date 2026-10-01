"""
In-memory state for active agent runs.

AgentRunState holds the threading.Event used to pause/resume the executor
during human approval gates. The module-level registry allows the approval
route to wake a paused executor in a different thread.

This state lives only for the duration of the process — if the server
restarts, orphan runs are detected and marked failed by main.py startup code.
"""
import threading
from dataclasses import dataclass, field


@dataclass
class AgentRunState:
    run_id: int
    # Set by the executor when waiting for approval; cleared after decision.
    approval_event: threading.Event = field(default_factory=threading.Event)
    # Which invoice is currently awaiting approval (one at a time).
    pending_approval_invoice_id: int | None = None


# Registry: run_id → AgentRunState
# The executor registers on start; the approval route looks up by run_id.
_active_runs: dict[int, AgentRunState] = {}
_lock = threading.Lock()


def register(run_id: int) -> AgentRunState:
    state = AgentRunState(run_id=run_id)
    with _lock:
        _active_runs[run_id] = state
    return state


def get(run_id: int) -> AgentRunState | None:
    return _active_runs.get(run_id)


def deregister(run_id: int) -> None:
    with _lock:
        _active_runs.pop(run_id, None)
