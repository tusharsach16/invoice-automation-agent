"""
Browser-backed tool for the executor: creates an invoice in the ERP via browser.

Implements the recovery protocol:
  submit_form → timeout → check_invoice_exists → recovered / retry

This is the only place in the agent that handles Playwright results.
portal.py provides the raw browser actions; this module adds recovery logic
and translates results into the processing_status vocabulary.
"""
import logging
from dataclasses import dataclass

from app.browser import portal

logger = logging.getLogger(__name__)

_MAX_RETRIES = 1   # one retry after a timeout + not-found check


@dataclass
class BrowserToolResult:
    processing_status: str   # 'verified' | 'recovered' | 'failed'
    invoice_number: str
    detail: str | None = None


def create_invoice_in_portal(source_invoice: dict) -> BrowserToolResult:
    """
    Submit the source invoice to the ERP via browser automation.

    States returned:
      'verified'  — submitted and confirmed on the resulting page.
      'recovered' — timed out, but ERP check found the record (no retry needed).
      'failed'    — could not be created after recovery attempt.
    """
    inv_number = source_invoice["invoice_number"]
    logger.info("Submitting invoice %s to ERP portal", inv_number)

    result = portal.submit_invoice_form(source_invoice)

    if result.success:
        logger.info("Invoice %s submitted and verified", inv_number)
        return BrowserToolResult(
            processing_status="verified",
            invoice_number=inv_number,
        )

    if not result.timed_out:
        # Non-timeout failure (e.g. network error, selector not found).
        logger.error("Invoice %s submission failed: %s", inv_number, result.error)
        return BrowserToolResult(
            processing_status="failed",
            invoice_number=inv_number,
            detail=result.error,
        )

    # Timeout path: the form may have submitted before the connection dropped.
    # Check the ERP before deciding whether to retry.
    logger.warning(
        "Timeout submitting %s — checking ERP before retry", inv_number
    )
    existence = portal.check_invoice_exists(inv_number)

    if existence.found:
        # ERP has the record. The server processed it; only the response was lost.
        # Do NOT submit again.
        logger.info(
            "Invoice %s recovered — found in ERP (amount=%.0f)",
            inv_number, existence.amount or 0,
        )
        return BrowserToolResult(
            processing_status="recovered",
            invoice_number=inv_number,
            detail="Timeout on submission; invoice found in ERP on recovery check",
        )

    # Not found — safe to retry once.
    logger.info("Invoice %s not in ERP after timeout, retrying once", inv_number)
    retry = portal.submit_invoice_form(source_invoice)

    if retry.success:
        return BrowserToolResult(
            processing_status="verified",
            invoice_number=inv_number,
            detail="Succeeded on retry after initial timeout",
        )

    logger.error(
        "Invoice %s failed after retry: %s", inv_number, retry.error
    )
    return BrowserToolResult(
        processing_status="failed",
        invoice_number=inv_number,
        detail=f"Timeout then retry failed: {retry.error}",
    )
