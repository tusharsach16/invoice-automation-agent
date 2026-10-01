import sqlite3
import pytest
from app.db.models import init_db
from app.agent.state import AgentRunState
from app.agent.executor import _process_invoice
from app.schemas.purchase_order import POMatchStatus


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    # Insert test vendor and source invoices
    conn.execute("INSERT INTO vendors (id, name, gstin) VALUES (1, 'Test Vendor', 'GSTIN001')")
    conn.execute(
        """
        INSERT INTO source_invoices (id, invoice_number, vendor_id, po_number, amount, invoice_date)
        VALUES (101, 'INV-TIMEOUT-1', 1, 'PO-1', 150000.0, '2026-10-01')
        """
    )
    conn.execute("INSERT INTO agent_runs (id, goal, plan_json, status) VALUES (1, 'Test', '{}', 'running')")
    conn.execute("INSERT INTO invoice_run_state (run_id, source_invoice_id) VALUES (1, 101)")
    conn.commit()
    yield conn
    conn.close()


def test_approval_timeout_semantics(db_conn, monkeypatch):
    """
    Ensure approval timeout marks approval_status='timed_out', processing_status='skipped',
    and logs outcome='timeout', without falsely marking it as rejected by human.
    """
    run_state = AgentRunState(run_id=1)

    invoice = {
        "source_invoice_id": 101,
        "invoice_number": "INV-TIMEOUT-1",
        "amount": 150000.0,
        "vendor_id": 1,
        "po_number": "PO-1",
    }
    po_results = {101: {"status": POMatchStatus.MATCH}}

    class DummyConn:
        def __init__(self, conn):
            self._conn = conn
        def close(self):
            pass
        def __getattr__(self, item):
            return getattr(self._conn, item)

    wrapped_conn = DummyConn(db_conn)

    # Monkeypatch get_connection to return wrapped_conn for in-memory testing
    monkeypatch.setattr("app.agent.executor.get_connection", lambda: wrapped_conn)
    # Monkeypatch wait timeout to return False immediately
    monkeypatch.setattr(run_state.approval_event, "wait", lambda timeout: False)

    conn = _process_invoice(
        wrapped_conn,
        run_id=1,
        invoice=invoice,
        po_results=po_results,
        run_state=run_state,
        threshold=100000.0,
    )

    # Check invoice_run_state
    state = conn.execute(
        "SELECT approval_status, processing_status FROM invoice_run_state WHERE run_id=1 AND source_invoice_id=101"
    ).fetchone()
    assert state["approval_status"] == "timed_out"
    assert state["processing_status"] == "skipped"

    # Check agent_actions
    actions = conn.execute(
        "SELECT action, outcome, detail FROM agent_actions WHERE run_id=1 ORDER BY id"
    ).fetchall()
    outcomes = [(a["action"], a["outcome"]) for a in actions]
    assert ("request_approval", "pending") in outcomes
    assert ("approval_decision", "timeout") in outcomes
    # Must NOT have recorded outcome as 'rejected'
    assert ("approval_decision", "rejected") not in outcomes


def test_human_rejection_semantics(db_conn, monkeypatch):
    """
    Ensure human rejection marks approval_status='rejected', processing_status='skipped',
    and logs outcome='rejected'.
    """
    run_state = AgentRunState(run_id=1)

    invoice = {
        "source_invoice_id": 101,
        "invoice_number": "INV-TIMEOUT-1",
        "amount": 150000.0,
        "vendor_id": 1,
        "po_number": "PO-1",
    }
    po_results = {101: {"status": POMatchStatus.MATCH}}

    class DummyConn:
        def __init__(self, conn):
            self._conn = conn
        def close(self):
            pass
        def __getattr__(self, item):
            return getattr(self._conn, item)

    wrapped_conn = DummyConn(db_conn)

    def mock_wait_and_reject(timeout):
        # Simulate human rejection written to DB
        db_conn.execute("UPDATE invoice_run_state SET approval_status='rejected' WHERE run_id=1 AND source_invoice_id=101")
        db_conn.commit()
        return True

    monkeypatch.setattr("app.agent.executor.get_connection", lambda: wrapped_conn)
    monkeypatch.setattr(run_state.approval_event, "wait", mock_wait_and_reject)

    conn = _process_invoice(
        wrapped_conn,
        run_id=1,
        invoice=invoice,
        po_results=po_results,
        run_state=run_state,
        threshold=100000.0,
    )

    state = conn.execute(
        "SELECT approval_status, processing_status FROM invoice_run_state WHERE run_id=1 AND source_invoice_id=101"
    ).fetchone()
    assert state["approval_status"] == "rejected"
    assert state["processing_status"] == "skipped"

    actions = conn.execute(
        "SELECT action, outcome FROM agent_actions WHERE run_id=1 ORDER BY id"
    ).fetchall()
    outcomes = [(a["action"], a["outcome"]) for a in actions]
    assert ("approval_decision", "rejected") in outcomes


def test_human_approval_proceeds_to_submission(db_conn, monkeypatch):
    """
    Ensure human approval marks approval_status='approved', logs outcome='approved',
    and proceeds to portal submission.
    """
    run_state = AgentRunState(run_id=1)

    invoice = {
        "source_invoice_id": 101,
        "invoice_number": "INV-TIMEOUT-1",
        "amount": 150000.0,
        "vendor_id": 1,
        "po_number": "PO-1",
    }
    po_results = {101: {"status": POMatchStatus.MATCH}}

    class DummyConn:
        def __init__(self, conn):
            self._conn = conn
        def close(self):
            pass
        def __getattr__(self, item):
            return getattr(self._conn, item)

    wrapped_conn = DummyConn(db_conn)

    def mock_wait_and_approve(timeout):
        db_conn.execute("UPDATE invoice_run_state SET approval_status='approved' WHERE run_id=1 AND source_invoice_id=101")
        db_conn.commit()
        return True

    from app.tools.browser_tools import BrowserToolResult
    monkeypatch.setattr("app.agent.executor.get_connection", lambda: wrapped_conn)
    monkeypatch.setattr(run_state.approval_event, "wait", mock_wait_and_approve)
    monkeypatch.setattr(
        "app.tools.browser_tools.create_invoice_in_portal",
        lambda inv: BrowserToolResult(processing_status="verified", invoice_number=inv["invoice_number"]),
    )

    conn = _process_invoice(
        wrapped_conn,
        run_id=1,
        invoice=invoice,
        po_results=po_results,
        run_state=run_state,
        threshold=100000.0,
    )

    state = conn.execute(
        "SELECT approval_status, processing_status FROM invoice_run_state WHERE run_id=1 AND source_invoice_id=101"
    ).fetchone()
    assert state["approval_status"] == "approved"
    assert state["processing_status"] == "verified"

    actions = conn.execute(
        "SELECT action, outcome FROM agent_actions WHERE run_id=1 ORDER BY id"
    ).fetchall()
    outcomes = [(a["action"], a["outcome"]) for a in actions]
    assert ("approval_decision", "approved") in outcomes
    assert ("submit_to_erp", "success") in outcomes
