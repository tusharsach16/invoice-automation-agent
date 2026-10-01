import pytest
from app.schemas.agent import ExecutionPlan, FilterConfig, validate_action_sequence


def test_valid_action_sequences():
    valid_sequences = [
        ["read_invoices"],
        ["read_invoices", "generate_report"],
        ["read_invoices", "check_purchase_orders"],
        ["read_invoices", "check_purchase_orders", "generate_report"],
        ["read_invoices", "check_purchase_orders", "create_invoices"],
        ["read_invoices", "check_purchase_orders", "create_invoices", "generate_report"],
        ["read_invoices", "check_purchase_orders", "flag_mismatches", "create_invoices", "generate_report"],
        ["read_invoices", "check_purchase_orders", "create_invoices", "flag_mismatches", "generate_report"],
    ]
    for actions in valid_sequences:
        plan = ExecutionPlan(filters=FilterConfig(), actions=actions)
        assert plan.actions == actions


def test_empty_actions_rejected():
    with pytest.raises(ValueError, match="at least one action"):
        ExecutionPlan(filters=FilterConfig(), actions=[])


def test_unknown_action_rejected():
    with pytest.raises(ValueError, match="Unknown actions not permitted"):
        ExecutionPlan(filters=FilterConfig(), actions=["read_invoices", "non_existent_action"])


def test_duplicate_actions_rejected():
    with pytest.raises(ValueError, match="Duplicate actions"):
        ExecutionPlan(filters=FilterConfig(), actions=["read_invoices", "read_invoices"])


def test_ordering_read_before_check_po():
    with pytest.raises(ValueError, match="'read_invoices' must precede 'check_purchase_orders'"):
        ExecutionPlan(filters=FilterConfig(), actions=["check_purchase_orders", "read_invoices"])


def test_missing_dependency_check_po_without_read():
    with pytest.raises(ValueError, match="'read_invoices' must precede 'check_purchase_orders'"):
        ExecutionPlan(filters=FilterConfig(), actions=["check_purchase_orders"])


def test_ordering_check_po_before_create_invoices():
    with pytest.raises(ValueError, match="'check_purchase_orders' must precede 'create_invoices'"):
        ExecutionPlan(filters=FilterConfig(), actions=["read_invoices", "create_invoices", "check_purchase_orders"])


def test_missing_dependency_create_invoices_without_check_po():
    with pytest.raises(ValueError, match="'check_purchase_orders' must precede 'create_invoices'"):
        ExecutionPlan(filters=FilterConfig(), actions=["read_invoices", "create_invoices"])


def test_flag_mismatches_before_check_po():
    with pytest.raises(ValueError, match="'check_purchase_orders' must precede 'flag_mismatches'"):
        ExecutionPlan(filters=FilterConfig(), actions=["read_invoices", "flag_mismatches", "check_purchase_orders"])


def test_generate_report_must_be_after_processing():
    with pytest.raises(ValueError, match="'generate_report' must occur after processing"):
        ExecutionPlan(
            filters=FilterConfig(),
            actions=["generate_report", "read_invoices", "check_purchase_orders"],
        )


def test_planner_plan_with_genai_client(monkeypatch):
    from unittest.mock import MagicMock
    from app.agent import planner
    from app.core.config import settings

    monkeypatch.setattr(settings, "google_api_key", "mock_key")

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"filters": {"min_amount": 10000}, "actions": ["read_invoices", "check_purchase_orders", "create_invoices", "generate_report"]}'
    mock_client.models.generate_content.return_value = mock_response

    monkeypatch.setattr("google.genai.Client", lambda api_key: mock_client)

    result_plan = planner.plan("Process all invoices above 10000")
    assert result_plan.filters.min_amount == 10000
    assert result_plan.actions == ["read_invoices", "check_purchase_orders", "create_invoices", "generate_report"]


def test_planner_retries_transient_503_and_succeeds(monkeypatch):
    from unittest.mock import MagicMock
    from app.agent import planner
    from app.core.config import settings
    from google.genai import errors

    monkeypatch.setattr(settings, "google_api_key", "mock_key")
    # Speed up test by setting base delay to 0
    monkeypatch.setattr(planner, "_BASE_DELAY_S", 0.0)
    monkeypatch.setattr("time.sleep", lambda s: None)

    mock_client = MagicMock()
    mock_success = MagicMock()
    mock_success.text = '{"filters": {}, "actions": ["read_invoices", "generate_report"]}'

    err_503 = errors.APIError(503, "503 UNAVAILABLE. High demand.", "UNAVAILABLE")

    # Fails twice with 503, then succeeds on 3rd attempt
    mock_client.models.generate_content.side_effect = [err_503, err_503, mock_success]
    monkeypatch.setattr("google.genai.Client", lambda api_key: mock_client)

    plan = planner.plan("Process invoices")
    assert plan.actions == ["read_invoices", "generate_report"]
    assert mock_client.models.generate_content.call_count == 3


def test_planner_does_not_retry_non_transient_404(monkeypatch):
    from unittest.mock import MagicMock
    from app.agent import planner
    from app.core.config import settings
    from google.genai import errors

    monkeypatch.setattr(settings, "google_api_key", "mock_key")
    monkeypatch.setattr("time.sleep", lambda s: None)

    mock_client = MagicMock()
    err_404 = errors.APIError(404, "404 NOT_FOUND. Model not found.", "NOT_FOUND")
    mock_client.models.generate_content.side_effect = err_404
    monkeypatch.setattr("google.genai.Client", lambda api_key: mock_client)

    with pytest.raises(ValueError, match="Gemini API request failed"):
        planner.plan("Process invoices")

    # Exactly 1 attempt made (no retries for 404)
    assert mock_client.models.generate_content.call_count == 1


def test_planner_extracts_specific_invoice(monkeypatch):
    from unittest.mock import MagicMock
    from app.agent import planner
    from app.core.config import settings

    monkeypatch.setattr(settings, "google_api_key", "mock_key")

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"filters": {"invoice_numbers": ["INV-2024-004"]}, "actions": ["read_invoices", "check_purchase_orders", "create_invoices", "generate_report"]}'
    mock_client.models.generate_content.return_value = mock_response

    monkeypatch.setattr("google.genai.Client", lambda api_key: mock_client)

    plan = planner.plan("Process invoice INV-2024-004.")
    assert plan.filters.invoice_numbers == ["INV-2024-004"]
    assert plan.actions == ["read_invoices", "check_purchase_orders", "create_invoices", "generate_report"]


def test_invoice_filtering_selection():
    import sqlite3
    from app.db.models import init_db
    from app.services import source_invoice_service
    from app.tools import invoice_tools

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    conn.execute("INSERT INTO vendors (id, name, gstin) VALUES (1, 'Vendor A', 'GST001')")
    conn.execute(
        """
        INSERT INTO source_invoices (id, invoice_number, vendor_id, po_number, amount, invoice_date)
        VALUES (1, 'INV-2024-001', 1, 'PO-1', 10000.0, '2026-10-01'),
               (2, 'INV-2024-002', 1, 'PO-2', 55000.0, '2026-10-01'),
               (3, 'INV-2024-004', 1, 'PO-4', 200000.0, '2026-10-01')
        """
    )
    conn.execute("INSERT INTO agent_runs (id, goal, plan_json, status) VALUES (1, 'Test', '{}', 'running')")
    conn.commit()

    # 1. Specific invoice target: "Process invoice INV-2024-004." selects ONLY INV-2024-004
    specific_filter = FilterConfig(invoice_numbers=["INV-2024-004"])
    invoices = invoice_tools.read_invoices(conn, 1, specific_filter)
    assert len(invoices) == 1
    assert invoices[0]["invoice_number"] == "INV-2024-004"

    # 2. Broad goal still processes multiple matching invoices
    broad_filter = FilterConfig(min_amount=50000.0)
    invoices_broad = source_invoice_service.get_all(conn, min_amount=broad_filter.min_amount)
    assert len(invoices_broad) == 2
    assert {inv["invoice_number"] for inv in invoices_broad} == {"INV-2024-002", "INV-2024-004"}

    # 3. Specific invoice + amount constraint applies both constraints
    # Matching both:
    both_match_filter = FilterConfig(invoice_numbers=["INV-2024-004"], min_amount=150000.0)
    invoices_both = source_invoice_service.get_all(
        conn,
        min_amount=both_match_filter.min_amount,
        invoice_numbers=both_match_filter.invoice_numbers,
    )
    assert len(invoices_both) == 1
    assert invoices_both[0]["invoice_number"] == "INV-2024-004"

    # Not matching amount (constraint fails):
    failing_amount_filter = FilterConfig(invoice_numbers=["INV-2024-004"], min_amount=250000.0)
    invoices_none = source_invoice_service.get_all(
        conn,
        min_amount=failing_amount_filter.min_amount,
        invoice_numbers=failing_amount_filter.invoice_numbers,
    )
    assert len(invoices_none) == 0

    conn.close()


