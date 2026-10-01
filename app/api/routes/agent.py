"""
Agent API endpoints: start a run, check status, submit approval decisions.
"""
import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse

from app.agent import executor, planner, state as run_state_module
from app.db.database import get_db
from app.schemas.agent import AgentGoal, ApprovalRequest, AgentRunOut, ApprovalDecision

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent")


def _db():
    with get_db() as conn:
        yield conn


def _get_run_or_404(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return dict(row)


@router.post("/run", status_code=202)
def start_run(
    goal: AgentGoal,
    background_tasks: BackgroundTasks,
    conn: sqlite3.Connection = Depends(_db),
):
    """
    Parse the goal, call the LLM planner once, validate the plan,
    then start the executor in a background thread.

    Returns immediately with run_id so the caller can poll /agent/runs/{id}.
    """
    try:
        plan = planner.plan(goal.goal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    cursor = conn.execute(
        "INSERT INTO agent_runs (goal, plan_json) VALUES (?, ?)",
        (goal.goal, plan.model_dump_json()),
    )
    run_id = cursor.lastrowid
    conn.commit()

    logger.info("run=%d created. Plan: %s", run_id, plan.model_dump_json())
    background_tasks.add_task(executor.run, run_id, goal.goal, plan)
    return {"run_id": run_id, "status": "running"}


@router.get("/runs/{run_id}", response_model=AgentRunOut)
def get_run(run_id: int, conn: sqlite3.Connection = Depends(_db)):
    run = _get_run_or_404(conn, run_id)

    states = conn.execute(
        """
        SELECT irs.source_invoice_id, si.invoice_number, si.amount,
               irs.approval_status, irs.processing_status,
               irs.mismatch_reason, irs.error_detail
        FROM invoice_run_state irs
        JOIN source_invoices si ON si.id = irs.source_invoice_id
        WHERE irs.run_id = ?
        ORDER BY si.amount DESC
        """,
        (run_id,),
    ).fetchall()

    return AgentRunOut(
        id=run["id"],
        goal=run["goal"],
        status=run["status"],
        started_at=run["started_at"],
        finished_at=run["finished_at"],
        plan_json=run["plan_json"],
        report_json=run["report_json"],
        invoice_states=[dict(s) for s in states],
    )


@router.post("/runs/{run_id}/approve", status_code=200)
def approve_invoice(
    run_id: int,
    body: ApprovalRequest,
    conn: sqlite3.Connection = Depends(_db),
):
    """
    Submit a human approval or rejection for a paused invoice.

    Writes the decision to invoice_run_state, then signals the executor
    thread to resume processing.
    """
    run = _get_run_or_404(conn, run_id)
    if run["status"] != "paused":
        raise HTTPException(
            status_code=409,
            detail=f"Run is not paused (status={run['status']})",
        )

    new_approval = (
        "approved" if body.decision == ApprovalDecision.APPROVE else "rejected"
    )
    conn.execute(
        """
        UPDATE invoice_run_state
        SET approval_status = ?, updated_at = datetime('now')
        WHERE run_id = ? AND source_invoice_id = ?
        """,
        (new_approval, run_id, body.source_invoice_id),
    )
    conn.commit()

    run_state = run_state_module.get(run_id)
    if run_state is None:
        raise HTTPException(
            status_code=409,
            detail="Executor is not running (server may have restarted)",
        )

    run_state.approval_event.set()
    logger.info(
        "run=%d invoice=%d approval decision: %s",
        run_id, body.source_invoice_id, new_approval,
    )
    return {"run_id": run_id, "invoice_id": body.source_invoice_id, "decision": new_approval}


@router.post("/runs/{run_id}/approve-form")
def approve_invoice_form(
    run_id: int,
    source_invoice_id: int = Form(...),
    decision: str = Form(...),
    conn: sqlite3.Connection = Depends(_db),
):
    """
    Form-based approval endpoint used by the HTML UI buttons.
    Delegates to the same logic as the JSON endpoint, then redirects back.
    """
    from app.schemas.agent import ApprovalDecision  # avoid circular at module level
    try:
        dec = ApprovalDecision(decision)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid decision: {decision}")

    body = ApprovalRequest(source_invoice_id=source_invoice_id, decision=dec)
    approve_invoice(run_id, body, conn)
    return RedirectResponse(url=f"/agent-runs/{run_id}", status_code=303)
