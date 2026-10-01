"""
Persistence layer for erp_invoices.

erp_invoices are created ONLY when the ERP application's POST /erp-invoices
route processes a form submission from the browser. portal.py never writes here
directly — it submits a form, and this service writes the result.

The verifier reads from here to confirm actual ERP state, independently
of what the agent's own logs claim.
"""
import sqlite3
from typing import Any


def create(
    conn: sqlite3.Connection,
    invoice_number: str,
    vendor_id: int,
    amount: float,
    po_number: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """
    Insert an ERP invoice record.

    Raises sqlite3.IntegrityError if invoice_number already exists.
    The route layer catches this and returns an appropriate error response.
    """
    conn.execute(
        """
        INSERT INTO erp_invoices (invoice_number, vendor_id, po_number, amount)
        VALUES (?, ?, ?, ?)
        """,
        (invoice_number, vendor_id, po_number, amount),
    )
    conn.commit()
    return get_by_invoice_number(conn, invoice_number)  # type: ignore[return-value]


def get_by_invoice_number(
    conn: sqlite3.Connection, invoice_number: str
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT e.id, e.invoice_number, e.vendor_id, v.name AS vendor_name,
               e.po_number, e.amount, e.entered_at
        FROM erp_invoices e
        JOIN vendors v ON v.id = e.vendor_id
        WHERE e.invoice_number = ?
        """,
        (invoice_number,),
    ).fetchone()
    return dict(row) if row else None


def get_all(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.id, e.invoice_number, e.vendor_id, v.name AS vendor_name,
               e.po_number, e.amount, e.entered_at
        FROM erp_invoices e
        JOIN vendors v ON v.id = e.vendor_id
        ORDER BY e.entered_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]
