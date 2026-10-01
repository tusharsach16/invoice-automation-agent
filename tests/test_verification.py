import sqlite3
import pytest
from app.db.models import init_db
from app.services.verification_service import verify


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    conn.execute("INSERT INTO vendors (id, name, gstin) VALUES (1, 'Vendor A', 'GST001')")
    conn.execute(
        """
        INSERT INTO source_invoices (id, invoice_number, vendor_id, po_number, amount, invoice_date)
        VALUES (1, 'INV-1', 1, 'PO-1', 10000.0, '2026-10-01'),
               (2, 'INV-2', 1, 'PO-2', 20000.0, '2026-10-01'),
               (3, 'INV-3', 1, 'PO-3', 30000.0, '2026-10-01')
        """
    )
    conn.execute("INSERT INTO agent_runs (id, goal, plan_json, status) VALUES (1, 'Process invoices', '{}', 'running')")
    conn.commit()
    yield conn
    conn.close()


def test_verification_handles_timed_out_and_recovered(db_conn):
    # Setup invoice run states:
    # INV-1: verified, in ERP
    # INV-2: recovered, in ERP
    # INV-3: skipped (timed_out), not in ERP
    db_conn.execute(
        """
        INSERT INTO invoice_run_state (run_id, source_invoice_id, approval_status, processing_status)
        VALUES (1, 1, 'not_required', 'verified'),
               (1, 2, 'approved', 'recovered'),
               (1, 3, 'timed_out', 'skipped')
        """
    )
    db_conn.execute(
        """
        INSERT INTO erp_invoices (id, invoice_number, vendor_id, po_number, amount)
        VALUES (1, 'INV-1', 1, 'PO-1', 10000.0),
               (2, 'INV-2', 1, 'PO-2', 20000.0)
        """
    )
    db_conn.commit()

    report = verify(db_conn, 1)

    assert report.passed is True
    assert report.verified_count == 1
    assert report.recovered_count == 1
    assert report.skipped_count == 1
    assert report.failed_count == 0

    # Verify run status was updated to completed
    run = db_conn.execute("SELECT status, finished_at FROM agent_runs WHERE id=1").fetchone()
    assert run["status"] == "completed"
    assert run["finished_at"] is not None

    # Check note for skipped invoice
    inv3_check = next(c for c in report.invoice_checks if c.invoice_number == "INV-3")
    assert inv3_check.note == "Skipped (timed out)"
