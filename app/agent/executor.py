"""
Agent executor: deterministic action dispatch after the plan is set.

No LLM calls happen here. The executor iterates the validated ExecutionPlan,
dispatches to tools, manages the human approval gate, and calls verification.

Runs in a background thread (FastAPI BackgroundTask). Pauses via threading.Event
when an invoice requires human approval. The /approve route resumes it.
"""
import json
import logging
import sqlite3

from app.agent import state as run_state_module
from app.core.config import settings
from app.db.database import get_connection
from app.schemas.agent import ExecutionPlan
from app.schemas.purchase_order import POMatchStatus
from app.services import verification_service
from app.tools import invoice_tools, browser_tools

logger = logging.getLogger(__name__)

# How long the executor waits for a human approval before timing out (seconds).
_APPROVAL_TIMEOUT_S = 3600


def _set_run_status(conn: sqlite3.Connection, run_id: int, status: str) -> None:
    conn.execute(
        "UPDATE agent_runs SET status = ? WHERE id = ?", (status, run_id)
    )
    conn.commit()


def _process_invoice(
    conn: sqlite3.Connection,
    run_id: int,
    invoice: dict,
    po_results: dict,
    run_state,
    threshold: float,
) -> sqlite3.Connection:
    """Process a single invoice through the approval gate and browser submission."""
    inv_id = invoice["source_invoice_id"]
    inv_number = invoice["invoice_number"]
    amount = invoice["amount"]
    po_result = po_results.get(inv_id, {})
    po_status = po_result.get("status", POMatchStatus.MATCH)
    mismatch_reason = po_result.get("reason")

    # ── Approval gate ─────────────────────────────────────────────────────
    needs_approval = amount >= threshold or po_status != POMatchStatus.MATCH

    if needs_approval:
        logger.info(
            "run=%d invoice=%s requires approval (amount=%.0f, po_status=%s)",
            run_id, inv_number, amount, po_status,
        )
        invoice_tools.update_invoice_state(
            conn, run_id, inv_id, approval_status="pending"
        )
        invoice_tools.log_action(
            conn, run_id, "request_approval", "pending", inv_id,
            detail=mismatch_reason or f"Amount ₹{amount:,.0f} >= threshold",
        )
        _set_run_status(conn, run_id, "paused")

        run_state.pending_approval_invoice_id = inv_id
        run_state.approval_event.clear()

        # Close database connection during approval wait to release locks and resources
        conn.commit()
        conn.close()
        try:
            # Block until the /approve route sets this event (or timeout after 1 hour).
            approved = run_state.approval_event.wait(timeout=_APPROVAL_TIMEOUT_S)
        finally:
            conn = get_connection()

        if not approved:
            # Timeout with no human decision — mark timed_out, skip and continue.
            logger.warning(
                "run=%d invoice=%s approval timed out, skipping", run_id, inv_number
            )
            invoice_tools.update_invoice_state(
                conn, run_id, inv_id, approval_status="timed_out", processing_status="skipped"
            )
            invoice_tools.log_action(
                conn, run_id, "approval_decision", "timeout", inv_id,
                detail="Timed out waiting for approval",
            )
            _set_run_status(conn, run_id, "running")
            return conn

        # Read the decision that the /approve route wrote to the DB.
        row = conn.execute(
            "SELECT approval_status FROM invoice_run_state WHERE run_id=? AND source_invoice_id=?",
            (run_id, inv_id),
        ).fetchone()
        decision = row["approval_status"] if row else "rejected"

        invoice_tools.log_action(
            conn, run_id, "approval_decision", decision, inv_id
        )
        _set_run_status(conn, run_id, "running")

        if decision == "rejected":
            invoice_tools.update_invoice_state(
                conn, run_id, inv_id, processing_status="skipped"
            )
            logger.info("run=%d invoice=%s rejected by human", run_id, inv_number)
            return conn

    # ── Browser submission ─────────────────────────────────────────────────
    logger.info("run=%d Submitting invoice %s to ERP", run_id, inv_number)
    invoice_tools.update_invoice_state(
        conn, run_id, inv_id, processing_status="submitted"
    )

    browser_result = browser_tools.create_invoice_in_portal(invoice)

    invoice_tools.update_invoice_state(
        conn, run_id, inv_id, processing_status=browser_result.processing_status,
        error_detail=browser_result.detail,
    )
    outcome = (
        "success" if browser_result.processing_status == "verified"
        else "recovered" if browser_result.processing_status == "recovered"
        else "failed"
    )
    invoice_tools.log_action(
        conn, run_id, "submit_to_erp", outcome, inv_id,
        detail=browser_result.detail or browser_result.processing_status,
    )
    return conn


def run(run_id: int, goal: str, plan: ExecutionPlan) -> None:
    """
    Entry point for the background executor thread.

    The plan has already been validated by the planner; execution is fully
    deterministic from this point forward.
    """
    from app.schemas.agent import validate_action_sequence
    validate_action_sequence(plan.actions)

    conn = get_connection()
    run_state = run_state_module.register(run_id)

    try:
        logger.info("run=%d Starting execution. Actions: %s", run_id, plan.actions)

        invoices: list[dict] = []
        po_results: dict = {}

        for action in plan.actions:
            if action == "read_invoices":
                invoices = invoice_tools.read_invoices(conn, run_id, plan.filters)
                if not invoices:
                    logger.info("run=%d No invoices matched filters, stopping.", run_id)
                    break

            elif action == "check_purchase_orders":
                po_results = invoice_tools.check_purchase_orders(conn, run_id, invoices)

            elif action == "create_invoices":
                pending = invoice_tools.get_pending_invoices(conn, run_id)
                for invoice in pending:
                    conn = _process_invoice(
                        conn, run_id, invoice, po_results, run_state,
                        threshold=settings.approval_threshold,
                    )

            elif action == "flag_mismatches":
                for inv in invoices:
                    inv_id = inv["id"]
                    po_r = po_results.get(inv_id, {})
                    if po_r.get("status") != POMatchStatus.MATCH:
                        invoice_tools.log_action(
                            conn, run_id, "flag_mismatch", "success", inv_id,
                            detail=po_r.get("reason"),
                        )

            elif action == "generate_report":
                invoice_tools.generate_report(conn, run_id)

        # Verification reads erp_invoices directly — independent of our own logs.
        verification_service.verify(conn, run_id)

    except Exception as exc:
        logger.exception("run=%d Executor failed: %s", run_id, exc)
        if conn:
            conn.execute(
                """UPDATE agent_runs
                   SET status='failed', report_json=?, finished_at=datetime('now')
                   WHERE id=?""",
                (json.dumps({"error": str(exc)}), run_id),
            )
            conn.commit()
    finally:
        run_state_module.deregister(run_id)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
