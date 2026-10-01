"""
DB-backed tools exposed to the executor.

All functions here are pure database operations — no browser, no LLM.
This makes them unit-testable with a real (in-memory) SQLite connection
without launching a browser or calling any external service.
"""
import json
import logging
import sqlite3
from dataclasses import asdict
from typing import Any

from app.schemas.purchase_order import POMatchStatus
from app.services import source_invoice_service, po_service
from app.schemas.agent import FilterConfig

logger = logging.getLogger(__name__)


def read_invoices(
    conn: sqlite3.Connection,
    run_id: int,
    filters: FilterConfig,
) -> list[dict[str, Any]]:
    """
    Fetch source invoices matching the plan's filters and create
    invoice_run_state rows for each (one per run, idempotent).
    """
    invoices = source_invoice_service.get_all(
        conn,
        min_amount=filters.min_amount,
        max_amount=filters.max_amount,
        vendor_ids=filters.vendor_ids,
    )

    for inv in invoices:
        conn.execute(
            """
            INSERT OR IGNORE INTO invoice_run_state (run_id, source_invoice_id)
            VALUES (?, ?)
            """,
            (run_id, inv["id"]),
        )
    conn.commit()

    conn.execute(
        "INSERT INTO agent_actions (run_id, action, outcome, detail) VALUES (?, ?, ?, ?)",
        (run_id, "read_invoices", "success", f"{len(invoices)} invoices loaded"),
    )
    conn.commit()
    logger.info("run=%d read_invoices: %d invoices in scope", run_id, len(invoices))
    return invoices


def check_purchase_orders(
    conn: sqlite3.Connection,
    run_id: int,
    invoices: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """
    Match each invoice to its PO and record results.
    Returns a dict of {source_invoice_id: POMatchResult dict}.
    """
    results: dict[int, dict[str, Any]] = {}

    for inv in invoices:
        match = po_service.match(conn, inv)
        results[inv["id"]] = asdict(match)

        if match.status != POMatchStatus.MATCH:
            conn.execute(
                """
                UPDATE invoice_run_state SET mismatch_reason = ?, updated_at = datetime('now')
                WHERE run_id = ? AND source_invoice_id = ?
                """,
                (match.reason, run_id, inv["id"]),
            )
        conn.execute(
            """
            INSERT INTO agent_actions (run_id, source_invoice_id, action, outcome, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                inv["id"],
                "check_po",
                "success" if match.status == POMatchStatus.MATCH else "failed",
                match.reason or "PO matched",
            ),
        )

    conn.commit()
    logger.info("run=%d check_purchase_orders: complete", run_id)
    return results


def update_invoice_state(
    conn: sqlite3.Connection,
    run_id: int,
    source_invoice_id: int,
    **kwargs: Any,
) -> None:
    """Generic state updater for invoice_run_state fields."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [run_id, source_invoice_id]
    conn.execute(
        f"""
        UPDATE invoice_run_state
        SET {set_clause}, updated_at = datetime('now')
        WHERE run_id = ? AND source_invoice_id = ?
        """,
        values,
    )
    conn.commit()


def log_action(
    conn: sqlite3.Connection,
    run_id: int,
    action: str,
    outcome: str,
    source_invoice_id: int | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_actions (run_id, source_invoice_id, action, outcome, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, source_invoice_id, action, outcome, detail),
    )
    conn.commit()


def get_pending_invoices(
    conn: sqlite3.Connection, run_id: int
) -> list[dict[str, Any]]:
    """Return invoice_run_state rows still in 'pending' processing status."""
    rows = conn.execute(
        """
        SELECT irs.source_invoice_id, irs.approval_status, irs.mismatch_reason,
               si.invoice_number, si.vendor_id, si.po_number, si.amount, si.description
        FROM invoice_run_state irs
        JOIN source_invoices si ON si.id = irs.source_invoice_id
        WHERE irs.run_id = ? AND irs.processing_status = 'pending'
        ORDER BY si.amount DESC
        """,
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def generate_report(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """Aggregate invoice_run_state rows into a summary dict."""
    rows = conn.execute(
        """
        SELECT irs.processing_status, irs.approval_status, irs.mismatch_reason,
               si.invoice_number, si.amount
        FROM invoice_run_state irs
        JOIN source_invoices si ON si.id = irs.source_invoice_id
        WHERE irs.run_id = ?
        """,
        (run_id,),
    ).fetchall()

    summary: dict[str, Any] = {
        "total": len(rows),
        "verified": 0, "recovered": 0,
        "failed": 0, "skipped": 0,
        "flagged": 0,
        "invoices": [],
    }
    for r in rows:
        d = dict(r)
        summary[d["processing_status"]] = summary.get(d["processing_status"], 0) + 1
        if d["mismatch_reason"]:
            summary["flagged"] += 1
        summary["invoices"].append(d)

    log_action(conn, run_id, "generate_report", "success",
               detail=json.dumps({k: v for k, v in summary.items() if k != "invoices"}))
    return summary
