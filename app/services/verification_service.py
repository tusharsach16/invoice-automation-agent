"""
Post-run verification: compares source invoice expectations against actual ERP state.

This service queries erp_invoices independently of the agent's own action log.
If the agent mis-logged a result, this will catch it.
Verification failure sets agent_run.status = 'failed' regardless of executor outcome.
"""
import json
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class InvoiceCheckResult:
    invoice_number: str
    source_amount: float
    erp_amount: float | None        # None = not found in ERP
    vendor_match: bool
    amount_match: bool
    found_in_erp: bool
    processing_status: str          # from invoice_run_state
    note: str | None = None


@dataclass
class VerificationReport:
    run_id: int
    passed: bool
    total_in_scope: int
    verified_count: int
    recovered_count: int
    failed_count: int
    skipped_count: int
    anomalies: list[str]
    invoice_checks: list[InvoiceCheckResult]
    summary: str


def verify(conn: sqlite3.Connection, run_id: int) -> VerificationReport:
    """
    For every source invoice in scope for this run, query erp_invoices to
    confirm the invoice was actually entered and amounts match.

    This is the ground-truth check — it does not trust invoice_run_state.
    """
    states = conn.execute(
        """
        SELECT irs.source_invoice_id, irs.processing_status,
               si.invoice_number, si.amount AS source_amount, si.vendor_id AS source_vendor
        FROM invoice_run_state irs
        JOIN source_invoices si ON si.id = irs.source_invoice_id
        WHERE irs.run_id = ?
        """,
        (run_id,),
    ).fetchall()

    checks: list[InvoiceCheckResult] = []
    anomalies: list[str] = []
    verified = recovered = failed = skipped = 0

    for state in states:
        inv_number = state["invoice_number"]
        source_amount = state["source_amount"]
        proc_status = state["processing_status"]

        erp_row = conn.execute(
            "SELECT amount, vendor_id FROM erp_invoices WHERE invoice_number = ?",
            (inv_number,),
        ).fetchone()

        if proc_status == "skipped":
            skipped += 1
            checks.append(InvoiceCheckResult(
                invoice_number=inv_number,
                source_amount=source_amount,
                erp_amount=None,
                vendor_match=True,
                amount_match=True,
                found_in_erp=False,
                processing_status=proc_status,
                note="Skipped (filtered or rejected)",
            ))
            continue

        if erp_row is None:
            # Agent claimed success/recovery but ERP has no record — real failure.
            if proc_status in ("verified", "recovered"):
                anomalies.append(
                    f"{inv_number}: agent reported '{proc_status}' but not found in ERP"
                )
            failed += 1
            checks.append(InvoiceCheckResult(
                invoice_number=inv_number,
                source_amount=source_amount,
                erp_amount=None,
                vendor_match=False,
                amount_match=False,
                found_in_erp=False,
                processing_status=proc_status,
                note="Not found in ERP",
            ))
            continue

        erp_amount = erp_row["amount"]
        vendor_match = erp_row["vendor_id"] == state["source_vendor"]
        amount_match = abs(erp_amount - source_amount) < 0.01

        if not vendor_match:
            anomalies.append(f"{inv_number}: vendor mismatch in ERP")
        if not amount_match:
            anomalies.append(
                f"{inv_number}: amount mismatch — source ₹{source_amount:,.0f}, ERP ₹{erp_amount:,.0f}"
            )

        if proc_status == "recovered":
            recovered += 1
        else:
            verified += 1

        checks.append(InvoiceCheckResult(
            invoice_number=inv_number,
            source_amount=source_amount,
            erp_amount=erp_amount,
            vendor_match=vendor_match,
            amount_match=amount_match,
            found_in_erp=True,
            processing_status=proc_status,
        ))

    # Check for duplicate erp_invoices (should be impossible given UNIQUE constraint,
    # but worth asserting as part of verification).
    dupes = conn.execute(
        """
        SELECT invoice_number, COUNT(*) as cnt
        FROM erp_invoices GROUP BY invoice_number HAVING cnt > 1
        """
    ).fetchall()
    for d in dupes:
        anomalies.append(f"DUPLICATE erp_invoice: {d['invoice_number']} ({d['cnt']} copies)")

    total = len(states)
    passed = len(anomalies) == 0 and failed == 0

    summary = (
        f"{verified} verified, {recovered} recovered, "
        f"{failed} failed, {skipped} skipped — "
        f"{'PASSED' if passed else 'FAILED'}"
    )

    report = VerificationReport(
        run_id=run_id,
        passed=passed,
        total_in_scope=total,
        verified_count=verified,
        recovered_count=recovered,
        failed_count=failed,
        skipped_count=skipped,
        anomalies=anomalies,
        invoice_checks=checks,
        summary=summary,
    )

    # Persist report and finalise run status.
    conn.execute(
        """
        UPDATE agent_runs
        SET status = ?, report_json = ?, finished_at = datetime('now')
        WHERE id = ?
        """,
        ("completed" if passed else "failed", json.dumps(asdict(report)), run_id),
    )
    conn.commit()
    return report
