"""
Purchase order lookup and matching against source invoice amounts.
"""
import sqlite3
from typing import Any

from app.schemas.purchase_order import POMatchResult, POMatchStatus


def get_by_po_number(
    conn: sqlite3.Connection, po_number: str
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT po.id, po.po_number, po.vendor_id, v.name AS vendor_name,
               po.amount, po.description, po.created_at
        FROM purchase_orders po
        JOIN vendors v ON v.id = po.vendor_id
        WHERE po.po_number = ?
        """,
        (po_number,),
    ).fetchone()
    return dict(row) if row else None


def get_all(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT po.id, po.po_number, po.vendor_id, v.name AS vendor_name,
               po.amount, po.description, po.created_at
        FROM purchase_orders po
        JOIN vendors v ON v.id = po.vendor_id
        ORDER BY po.po_number
        """
    ).fetchall()
    return [dict(r) for r in rows]


def match(
    conn: sqlite3.Connection, source_invoice: dict[str, Any]
) -> POMatchResult:
    """
    Check whether the source invoice's claimed PO exists and amounts agree.

    Returns a POMatchResult indicating: match, amount_mismatch, or not_found.
    A mismatch triggers the approval gate in the executor.
    """
    po_number = source_invoice.get("po_number")
    invoice_amount = source_invoice["amount"]
    inv_id = source_invoice["id"]

    if not po_number:
        return POMatchResult(
            source_invoice_id=inv_id,
            po_number=None,
            status=POMatchStatus.NOT_FOUND,
            invoice_amount=invoice_amount,
            reason="Invoice does not reference a PO",
        )

    po = get_by_po_number(conn, po_number)
    if po is None:
        return POMatchResult(
            source_invoice_id=inv_id,
            po_number=po_number,
            status=POMatchStatus.NOT_FOUND,
            invoice_amount=invoice_amount,
            reason=f"PO '{po_number}' not found in ERP",
        )

    # Allow a small float comparison tolerance.
    if abs(po["amount"] - invoice_amount) < 0.01:
        return POMatchResult(
            source_invoice_id=inv_id,
            po_number=po_number,
            status=POMatchStatus.MATCH,
            po_amount=po["amount"],
            invoice_amount=invoice_amount,
        )

    return POMatchResult(
        source_invoice_id=inv_id,
        po_number=po_number,
        status=POMatchStatus.AMOUNT_MISMATCH,
        po_amount=po["amount"],
        invoice_amount=invoice_amount,
        reason=f"Invoice ₹{invoice_amount:,.0f} vs PO ₹{po['amount']:,.0f}",
    )
