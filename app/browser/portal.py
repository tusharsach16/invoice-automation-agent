"""
All Playwright browser interactions live here.

This module knows about the ERP's HTML structure (form IDs, page URLs, selectors).
It does NOT import sqlite3 and never touches the database.
It submits forms, reads pages, and returns structured results to browser_tools.py.

The ERP's business logic runs server-side — portal.py is purely a browser client.
"""
import logging
from dataclasses import dataclass

from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# How long to wait for page elements before raising PlaywrightTimeout.
_TIMEOUT_MS = 10_000
_ERP_BASE_URL = "http://localhost:8000"


@dataclass
class SubmitResult:
    success: bool
    invoice_number: str
    timed_out: bool = False
    error: str | None = None


@dataclass
class ExistenceResult:
    found: bool
    invoice_number: str
    amount: float | None = None


def _navigate_to_create(page: Page) -> None:
    page.goto(f"{_ERP_BASE_URL}/create-invoice", timeout=_TIMEOUT_MS)
    page.wait_for_selector("#invoice_number", timeout=_TIMEOUT_MS)


def submit_invoice_form(invoice: dict) -> SubmitResult:
    """
    Navigate to the create-invoice page, fill every field, and submit the form.

    On success, the server redirects to /erp-invoices/{invoice_number}.
    We confirm success by reading the invoice_number shown on that page.

    Returns SubmitResult with timed_out=True if Playwright times out —
    the caller (browser_tools) must then call check_invoice_exists() to
    determine whether the server actually processed the request.
    """
    inv_number = invoice["invoice_number"]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            _navigate_to_create(page)

            page.select_option("#vendor_id", str(invoice["vendor_id"]))
            page.fill("#invoice_number", inv_number)
            page.fill("#po_number", invoice.get("po_number") or "")
            page.fill("#amount", str(invoice["amount"]))
            page.fill("#description", invoice.get("description") or "")

            page.click("#submit-invoice", timeout=_TIMEOUT_MS)
            # After successful POST, server sends 303 redirect to detail page.
            page.wait_for_url(
                f"{_ERP_BASE_URL}/erp-invoices/{inv_number}",
                timeout=_TIMEOUT_MS,
            )
            page.wait_for_selector("#erp-invoice-number", timeout=_TIMEOUT_MS)
            confirmed = page.inner_text("#erp-invoice-number").strip()

            browser.close()
            return SubmitResult(
                success=(confirmed == inv_number),
                invoice_number=inv_number,
                error=None if confirmed == inv_number else f"Confirmed number was '{confirmed}'",
            )

        except PlaywrightTimeout as exc:
            browser.close()
            logger.warning(
                "Playwright timeout submitting %s: %s", inv_number, exc
            )
            return SubmitResult(
                success=False,
                invoice_number=inv_number,
                timed_out=True,
                error=str(exc)[:200],
            )
        except Exception as exc:
            browser.close()
            return SubmitResult(
                success=False,
                invoice_number=inv_number,
                error=str(exc)[:200],
            )


def check_invoice_exists(invoice_number: str) -> ExistenceResult:
    """
    Navigate to the ERP invoice detail page and check if the record exists.

    Used after a timeout to determine whether the server processed the
    submission before the connection dropped. If found, we treat the
    submission as recovered and do NOT retry.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            response = page.goto(
                f"{_ERP_BASE_URL}/erp-invoices/{invoice_number}",
                timeout=_TIMEOUT_MS,
            )
            if response and response.status == 404:
                browser.close()
                return ExistenceResult(found=False, invoice_number=invoice_number)

            page.wait_for_selector("#erp-invoice-number", timeout=_TIMEOUT_MS)
            amount_text = page.inner_text("#erp-invoice-amount").strip()
            amount = float(amount_text.replace(",", "").replace("₹", ""))
            browser.close()
            return ExistenceResult(
                found=True, invoice_number=invoice_number, amount=amount
            )

        except (PlaywrightTimeout, Exception):
            browser.close()
            return ExistenceResult(found=False, invoice_number=invoice_number)
