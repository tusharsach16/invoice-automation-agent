"""
Read access to source_invoices.

Source invoices are immutable input data — this service is read-only.
The agent reads these to determine what to process.
"""
import sqlite3
from typing import Any


def get_all(
    conn: sqlite3.Connection,
    min_amount: float | None = None,
    max_amount: float | None = None,
    vendor_ids: list[int] | None = None,
    invoice_numbers: list[str] | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            si.id, si.invoice_number, si.vendor_id, v.name AS vendor_name,
            si.po_number, si.amount, si.description, si.invoice_date
        FROM source_invoices si
        JOIN vendors v ON v.id = si.vendor_id
        WHERE 1=1
    """
    params: list[Any] = []

    if min_amount is not None:
        query += " AND si.amount >= ?"
        params.append(min_amount)
    if max_amount is not None:
        query += " AND si.amount <= ?"
        params.append(max_amount)
    if vendor_ids:
        placeholders = ",".join("?" * len(vendor_ids))
        query += f" AND si.vendor_id IN ({placeholders})"
        params.extend(vendor_ids)
    if invoice_numbers:
        placeholders = ",".join("?" * len(invoice_numbers))
        query += f" AND si.invoice_number IN ({placeholders})"
        params.extend(invoice_numbers)

    query += " ORDER BY si.amount DESC"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_by_id(conn: sqlite3.Connection, source_invoice_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT si.id, si.invoice_number, si.vendor_id, v.name AS vendor_name,
               si.po_number, si.amount, si.description, si.invoice_date
        FROM source_invoices si
        JOIN vendors v ON v.id = si.vendor_id
        WHERE si.id = ?
        """,
        (source_invoice_id,),
    ).fetchone()
    return dict(row) if row else None
