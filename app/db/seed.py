"""
Synthetic seed data for development and demo.

Uses INSERT OR IGNORE so re-seeding is safe (idempotent).
Does NOT seed erp_invoices — that table starts empty and is populated
only through browser form submissions during an agent run.

Data is designed to exercise all code paths:
  - Happy path (auto-approved, exact PO match)
  - High-value requiring human approval (>= ₹1,00,000)
  - Amount mismatches (triggers approval gate)
  - Missing PO references (triggers approval gate)
  - Below-threshold invoices (filtered out by example goal)
"""
import sqlite3

_VENDORS = [
    (1, "Sharma Tech Supplies", "27AABCS1234A1Z5"),
    (2, "Mehta Industrial Corp", "06AABCM5678B2Y3"),
    (3, "Kapoor Office Solutions", "29AABCK9012C3X1"),
    (4, "Patel Manufacturing Ltd", "24AABCP3456D4W9"),
    (5, "Gupta Logistics Ltd", "07AABCG7890E5V7"),
]

_PURCHASE_ORDERS = [
    # (po_number, vendor_id, amount, description)
    ("PO-2026-001", 1, 75_000.00,  "Tech Equipment Procurement"),
    ("PO-2026-002", 2, 1_50_000.00, "Industrial Parts Q3"),
    ("PO-2026-003", 3, 35_000.00,  "Office Supplies Restock"),
    ("PO-2026-004", 4, 2_00_000.00, "Manufacturing Components"),
    ("PO-2026-005", 5, 45_000.00,  "Logistics Services Q3"),
    ("PO-2026-006", 1, 1_20_000.00, "Software Licenses Annual"),
    ("PO-2026-007", 2, 60_000.00,  "Maintenance Services"),
    ("PO-2026-008", 3, 25_000.00,  "Stationery and Consumables"),
    ("PO-2026-009", 4, 90_000.00,  "Raw Materials Batch 4"),
    ("PO-2026-010", 5, 55_000.00,  "Courier Services Q3"),
]

_SOURCE_INVOICES = [
    # (invoice_number, vendor_id, po_number, amount, description, invoice_date)
    # ── Category A: Exact PO match, ₹50k–₹1L → auto-processed ──────────
    ("INV-2026-001", 1, "PO-2026-001", 75_000.00,  "Tech equipment as per PO",        "2026-09-01"),
    ("INV-2026-007", 2, "PO-2026-007", 60_000.00,  "Maintenance services September",  "2026-09-07"),
    ("INV-2026-009", 4, "PO-2026-009", 90_000.00,  "Raw materials batch 4",           "2026-09-09"),
    ("INV-2026-010", 5, "PO-2026-010", 55_000.00,  "Courier services Q3",             "2026-09-10"),

    # ── Category B: Exact PO match, >₹1L → requires human approval ──────
    ("INV-2026-002", 2, "PO-2026-002", 1_50_000.00, "Industrial parts Q3",            "2026-09-02"),
    ("INV-2026-004", 4, "PO-2026-004", 2_00_000.00, "Manufacturing components",       "2026-09-04"),
    ("INV-2026-006", 1, "PO-2026-006", 1_20_000.00, "Annual software licenses",       "2026-09-06"),

    # ── Category C: Amount mismatch → flagged, approval gate ────────────
    ("INV-2026-011", 1, "PO-2026-001", 82_500.00,  "Tech equipment (10% above PO)",   "2026-09-11"),
    ("INV-2026-013", 4, "PO-2026-009", 63_000.00,  "Raw materials partial delivery",  "2026-09-13"),
    ("INV-2026-020", 2, "PO-2026-007", 58_500.00,  "Maintenance + travel expenses",   "2026-10-03"),

    # ── Category D: Amount mismatch >₹1L → flagged + approval ───────────
    ("INV-2026-012", 2, "PO-2026-002", 1_35_000.00, "Industrial parts (revised)",     "2026-09-12"),

    # ── Category E: No PO reference → flagged, approval gate ────────────
    ("INV-2026-014", 3, None,          55_000.00,  "Ad-hoc office renovation",        "2026-09-14"),

    # ── Category F: No PO, >₹1L → flagged + approval ────────────────────
    ("INV-2026-015", 5, None,         1_10_000.00, "Emergency freight surcharge",     "2026-09-15"),

    # ── Category G: Below ₹50k → filtered out by example goal ───────────
    ("INV-2026-003", 3, "PO-2026-003", 35_000.00,  "Office supplies restock",         "2026-09-03"),
    ("INV-2026-005", 5, "PO-2026-005", 45_000.00,  "Logistics services Q3",           "2026-09-05"),
    ("INV-2026-008", 3, "PO-2026-008", 25_000.00,  "Stationery and consumables",      "2026-09-08"),
    ("INV-2026-016", 1, "PO-2026-001", 38_000.00,  "Replacement parts (small order)", "2026-09-16"),
    ("INV-2026-017", 4, "PO-2026-009", 42_000.00,  "Raw materials sample batch",      "2026-09-17"),
    ("INV-2026-018", 2, "PO-2026-007", 30_000.00,  "Maintenance visit",               "2026-09-18"),

    # ── Category H: Recovery demo — exact match, used to simulate timeout ─
    ("INV-2026-019", 5, "PO-2026-010", 55_000.00,  "Courier services supplemental",   "2026-09-19"),
]


def seed_all(conn: sqlite3.Connection) -> None:
    """Insert all reference data. Safe to call multiple times."""
    conn.executemany(
        "INSERT OR IGNORE INTO vendors (id, name, gstin) VALUES (?, ?, ?)",
        _VENDORS,
    )
    conn.executemany(
        """INSERT OR IGNORE INTO purchase_orders (po_number, vendor_id, amount, description)
           VALUES (?, ?, ?, ?)""",
        _PURCHASE_ORDERS,
    )
    conn.executemany(
        """INSERT OR IGNORE INTO source_invoices
           (invoice_number, vendor_id, po_number, amount, description, invoice_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        _SOURCE_INVOICES,
    )
    for inv in _SOURCE_INVOICES:
        conn.execute(
            """UPDATE source_invoices
               SET vendor_id = ?, po_number = ?, amount = ?, description = ?, invoice_date = ?
               WHERE invoice_number = ?""",
            (inv[1], inv[2], inv[3], inv[4], inv[5], inv[0]),
        )
    conn.commit()
