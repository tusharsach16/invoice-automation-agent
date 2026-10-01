"""
Database schema definitions and initialisation.

init_db() is idempotent — safe to call on every startup.
Tables are created in dependency order (vendors before invoices, etc.).
"""
import sqlite3


_DDL = [
    # ── Shared reference data ─────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS vendors (
        id      INTEGER PRIMARY KEY,
        name    TEXT    NOT NULL,
        gstin   TEXT    NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id          INTEGER PRIMARY KEY,
        po_number   TEXT    NOT NULL UNIQUE,
        vendor_id   INTEGER NOT NULL REFERENCES vendors(id),
        amount      REAL    NOT NULL CHECK (amount > 0),
        description TEXT,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_po_vendor ON purchase_orders(vendor_id)",

    # ── Source invoices (agent INPUT — seeded, immutable) ─────────────────
    # These are vendor-sent documents in the operator's inbox.
    # The agent reads these; it never creates or modifies them.
    # Verification checks each in-scope source invoice against erp_invoices.
    """
    CREATE TABLE IF NOT EXISTS source_invoices (
        id              INTEGER PRIMARY KEY,
        invoice_number  TEXT    NOT NULL UNIQUE,
        vendor_id       INTEGER NOT NULL REFERENCES vendors(id),
        po_number       TEXT,
        amount          REAL    NOT NULL CHECK (amount > 0),
        description     TEXT,
        invoice_date    TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_src_vendor ON source_invoices(vendor_id)",
    "CREATE INDEX IF NOT EXISTS idx_src_amount  ON source_invoices(amount)",

    # ── ERP invoices (agent OUTPUT — created via browser form submission) ──
    # Written only when the ERP's POST /erp-invoices route processes a
    # form submission. portal.py fills the form; erp_invoice_service writes here.
    # The verifier reads this table independently to confirm actual ERP state.
    """
    CREATE TABLE IF NOT EXISTS erp_invoices (
        id              INTEGER PRIMARY KEY,
        invoice_number  TEXT    NOT NULL UNIQUE,
        vendor_id       INTEGER NOT NULL REFERENCES vendors(id),
        po_number       TEXT,
        amount          REAL    NOT NULL CHECK (amount > 0),
        entered_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_erp_number ON erp_invoices(invoice_number)",

    # ── Agent run log ─────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_runs (
        id          INTEGER PRIMARY KEY,
        goal        TEXT    NOT NULL,
        plan_json   TEXT    NOT NULL DEFAULT '{}',
        status      TEXT    NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'paused', 'completed', 'failed')),
        started_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        finished_at TEXT,
        report_json TEXT
    )
    """,

    # ── Per-invoice state within a run ────────────────────────────────────
    # approval_status  = human decision gate (was a person asked; what did they say?)
    # processing_status = browser/technical outcome (what did Playwright actually do?)
    # These are intentionally separate: a human can approve an invoice that
    # the browser then fails to submit due to a timeout.
    """
    CREATE TABLE IF NOT EXISTS invoice_run_state (
        id                  INTEGER PRIMARY KEY,
        run_id              INTEGER NOT NULL REFERENCES agent_runs(id),
        source_invoice_id   INTEGER NOT NULL REFERENCES source_invoices(id),
        approval_status     TEXT    NOT NULL DEFAULT 'not_required'
                            CHECK (approval_status IN (
                                'not_required', 'pending', 'approved', 'rejected'
                            )),
        processing_status   TEXT    NOT NULL DEFAULT 'pending'
                            CHECK (processing_status IN (
                                'pending', 'submitted', 'verified',
                                'recovered', 'failed', 'skipped'
                            )),
        mismatch_reason     TEXT,
        error_detail        TEXT,
        updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (run_id, source_invoice_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_irs_run ON invoice_run_state(run_id)",

    # ── Append-only action log ────────────────────────────────────────────
    # Every discrete action is recorded here, including recovery steps.
    # invoice_run_state = current state; agent_actions = how we got there.
    """
    CREATE TABLE IF NOT EXISTS agent_actions (
        id                  INTEGER PRIMARY KEY,
        run_id              INTEGER NOT NULL REFERENCES agent_runs(id),
        source_invoice_id   INTEGER REFERENCES source_invoices(id),
        action              TEXT    NOT NULL,
        outcome             TEXT    NOT NULL
                            CHECK (outcome IN (
                                'success', 'failed', 'timeout', 'recovered',
                                'skipped', 'approved', 'rejected', 'pending'
                            )),
        detail              TEXT,
        created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_aa_run     ON agent_actions(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_aa_invoice ON agent_actions(source_invoice_id)",
]


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes. Safe to call multiple times."""
    for statement in _DDL:
        conn.execute(statement)
    conn.commit()
