"""
All Playwright browser interactions live here.

This module knows about the ERP's HTML structure (form IDs, page URLs, selectors).
It does NOT import sqlite3 and never touches the database.
It submits forms, reads pages, and returns structured results to browser_tools.py.

The ERP's business logic runs server-side — portal.py is purely a browser client.
"""
import json
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
    check_failed: bool = False
    error: str | None = None


def _navigate_to_create(page: Page) -> None:
    page.goto(f"{_ERP_BASE_URL}/create-invoice", timeout=_TIMEOUT_MS)
    page.wait_for_selector("#invoice_number", timeout=_TIMEOUT_MS)


def submit_invoice_form(invoice: dict) -> SubmitResult:
    """
    Navigate to the create-invoice page, fill every field, and submit the form.

    On success, the server redirects to /erp-invoices/{invoice_number}.
    We confirm success by verifying the invoice_number on the resulting detail page.

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

            # Confirm detail page loaded and verify invoice number from HTML element, JSON, or body
            confirmed_num = None
            if page.locator("#erp-invoice-number").count() > 0:
                confirmed_num = page.inner_text("#erp-invoice-number").strip()
            else:
                body_text = page.inner_text("body").strip()
                try:
                    data = json.loads(body_text)
                    confirmed_num = data.get("invoice_number")
                except Exception:
                    if inv_number in body_text:
                        confirmed_num = inv_number

            success = (confirmed_num == inv_number)
            browser.close()
            return SubmitResult(
                success=success,
                invoice_number=inv_number,
                error=None if success else f"Could not confirm invoice number '{inv_number}' on detail page",
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

    Used before submission (idempotency check) and after a timeout (recovery check).
    Distinguishes clearly between:
      found=True                     (HTTP 200 / invoice confirmed)
      found=False, check_failed=False (HTTP 404 / confirmed not found)
      check_failed=True              (network / server error / timeout)
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
                return ExistenceResult(found=False, invoice_number=invoice_number, check_failed=False)

            if response and response.status >= 500:
                browser.close()
                return ExistenceResult(
                    found=False,
                    invoice_number=invoice_number,
                    check_failed=True,
                    error=f"HTTP {response.status} loading invoice detail",
                )

            # Check if page rendered the "Invoice Not Found" alert
            if page.locator(".alert-error").count() > 0:
                browser.close()
                return ExistenceResult(found=False, invoice_number=invoice_number, check_failed=False)

            # HTTP 200 on detail URL confirms existence; extract amount if available
            amount: float | None = None
            if page.locator("#erp-invoice-amount").count() > 0:
                try:
                    amount_text = page.inner_text("#erp-invoice-amount").strip()
                    amount = float(amount_text.replace(",", "").replace("₹", "").replace("INR", "").strip())
                except Exception:
                    pass

            if amount is None:
                body_text = page.inner_text("body").strip()
                try:
                    data = json.loads(body_text)
                    if "amount" in data and data["amount"] is not None:
                        amount = float(data["amount"])
                except Exception:
                    pass

            browser.close()
            return ExistenceResult(
                found=True,
                invoice_number=invoice_number,
                amount=amount,
                check_failed=False,
            )

        except PlaywrightTimeout as exc:
            browser.close()
            logger.warning("Timeout checking invoice %s existence: %s", invoice_number, exc)
            return ExistenceResult(
                found=False,
                invoice_number=invoice_number,
                check_failed=True,
                error=f"Timeout: {exc}",
            )
        except Exception as exc:
            browser.close()
            logger.error("Error checking invoice %s existence: %s", invoice_number, exc)
            return ExistenceResult(
                found=False,
                invoice_number=invoice_number,
                check_failed=True,
                error=str(exc),
            )
