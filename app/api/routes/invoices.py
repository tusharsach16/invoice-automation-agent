"""
REST endpoints for source invoices and ERP invoices.

POST /erp-invoices is the form submission target that Playwright automates.
This route is the application's business path for creating ERP invoice records —
portal.py fills the form, this route validates it, erp_invoice_service writes it.
"""
import sqlite3
import logging

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse

from app.db.database import get_db
from app.services import source_invoice_service, erp_invoice_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _db():
    with get_db() as conn:
        yield conn


@router.get("/source-invoices")
def list_source_invoices(
    min_amount: float | None = None,
    conn: sqlite3.Connection = Depends(_db),
):
    return source_invoice_service.get_all(conn, min_amount=min_amount)


@router.get("/source-invoices/{invoice_id}")
def get_source_invoice(invoice_id: int, conn: sqlite3.Connection = Depends(_db)):
    inv = source_invoice_service.get_by_id(conn, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Source invoice not found")
    return inv


@router.get("/erp-invoices")
def list_erp_invoices(conn: sqlite3.Connection = Depends(_db)):
    return erp_invoice_service.get_all(conn)


@router.get("/erp-invoices/{invoice_number}")
def get_erp_invoice(invoice_number: str, conn: sqlite3.Connection = Depends(_db)):
    inv = erp_invoice_service.get_by_invoice_number(conn, invoice_number)
    if not inv:
        raise HTTPException(status_code=404, detail="ERP invoice not found")
    return inv


@router.post("/erp-invoices")
def create_erp_invoice(
    invoice_number: str = Form(...),
    vendor_id: int = Form(...),
    po_number: str = Form(""),
    amount: float = Form(...),
    description: str = Form(""),
    conn: sqlite3.Connection = Depends(_db),
):
    """
    Receives a form submission from the browser (filled by Playwright or manually).
    Validates inputs, writes to erp_invoices via erp_invoice_service,
    then redirects to the invoice detail page.

    Playwright follows the redirect and reads the detail page to confirm success.
    """
    po_number_clean = po_number.strip() or None
    description_clean = description.strip() or None

    if amount <= 0:
        raise HTTPException(status_code=422, detail="Amount must be positive")

    try:
        erp_invoice_service.create(
            conn,
            invoice_number=invoice_number.strip(),
            vendor_id=vendor_id,
            amount=amount,
            po_number=po_number_clean,
            description=description_clean,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Invoice '{invoice_number}' already exists in ERP",
        )

    logger.info("ERP invoice created: %s (₹%s)", invoice_number, amount)
    # Redirect to detail page — Playwright reads this page to confirm creation.
    return RedirectResponse(
        url=f"/erp-invoices/{invoice_number.strip()}",
        status_code=303,
    )
