import pytest
from app.browser.portal import ExistenceResult, SubmitResult
from app.tools import browser_tools


def test_idempotency_precheck_skips_when_already_in_erp(monkeypatch):
    """
    If invoice already exists in ERP before submission, do not call submit_invoice_form.
    Return 'recovered' with descriptive detail.
    """
    submitted = []

    def mock_check_invoice_exists(inv_num):
        return ExistenceResult(found=True, invoice_number=inv_num, amount=50000.0, check_failed=False)

    def mock_submit_invoice_form(inv):
        submitted.append(inv)
        return SubmitResult(success=True, invoice_number=inv["invoice_number"])

    monkeypatch.setattr("app.browser.portal.check_invoice_exists", mock_check_invoice_exists)
    monkeypatch.setattr("app.browser.portal.submit_invoice_form", mock_submit_invoice_form)

    source_invoice = {"invoice_number": "INV-EXISTING-1", "amount": 50000.0, "vendor_id": 1}
    result = browser_tools.create_invoice_in_portal(source_invoice)

    assert result.processing_status == "recovered"
    assert "already exists in ERP" in result.detail
    assert len(submitted) == 0  # Form was never submitted


def test_precheck_fails_on_server_error(monkeypatch):
    """
    If pre-check cannot contact the ERP (check_failed=True), do not proceed with submission.
    """
    def mock_check_invoice_exists(inv_num):
        return ExistenceResult(found=False, invoice_number=inv_num, check_failed=True, error="HTTP 500 Internal Error")

    monkeypatch.setattr("app.browser.portal.check_invoice_exists", mock_check_invoice_exists)

    source_invoice = {"invoice_number": "INV-ERR-1", "amount": 50000.0, "vendor_id": 1}
    result = browser_tools.create_invoice_in_portal(source_invoice)

    assert result.processing_status == "failed"
    assert "ERP existence check failed" in result.detail


def test_post_timeout_check_failed_does_not_retry(monkeypatch):
    """
    If submission times out, and the subsequent existence check fails with network/server error,
    do NOT retry submission blindly. Return failed.
    """
    submit_calls = []

    def mock_submit_invoice_form(inv):
        submit_calls.append(inv)
        return SubmitResult(success=False, invoice_number=inv["invoice_number"], timed_out=True, error="Timeout")

    def mock_check_invoice_exists(inv_num):
        # First call is pre-check (not found), second call is post-timeout check (error)
        if len(submit_calls) == 0:
            return ExistenceResult(found=False, invoice_number=inv_num, check_failed=False)
        return ExistenceResult(found=False, invoice_number=inv_num, check_failed=True, error="Connection reset")

    monkeypatch.setattr("app.browser.portal.submit_invoice_form", mock_submit_invoice_form)
    monkeypatch.setattr("app.browser.portal.check_invoice_exists", mock_check_invoice_exists)

    source_invoice = {"invoice_number": "INV-TIMEOUT-ERR", "amount": 50000.0, "vendor_id": 1}
    result = browser_tools.create_invoice_in_portal(source_invoice)

    assert result.processing_status == "failed"
    assert "retry aborted" in result.detail
    # Exactly 1 submission attempt was made (no blind retry)
    assert len(submit_calls) == 1


def test_post_timeout_found_recovers(monkeypatch):
    """
    If submission times out, and the check finds the invoice in ERP, recover without retry.
    """
    submit_calls = []

    def mock_submit_invoice_form(inv):
        submit_calls.append(inv)
        return SubmitResult(success=False, invoice_number=inv["invoice_number"], timed_out=True, error="Timeout")

    def mock_check_invoice_exists(inv_num):
        if len(submit_calls) == 0:
            return ExistenceResult(found=False, invoice_number=inv_num, check_failed=False)
        return ExistenceResult(found=True, invoice_number=inv_num, amount=50000.0, check_failed=False)

    monkeypatch.setattr("app.browser.portal.submit_invoice_form", mock_submit_invoice_form)
    monkeypatch.setattr("app.browser.portal.check_invoice_exists", mock_check_invoice_exists)

    source_invoice = {"invoice_number": "INV-TIMEOUT-RECOV", "amount": 50000.0, "vendor_id": 1}
    result = browser_tools.create_invoice_in_portal(source_invoice)

    assert result.processing_status == "recovered"
    assert len(submit_calls) == 1


def test_existence_result_dataclass():
    res_found = ExistenceResult(found=True, invoice_number="INV-1", amount=1000.0)
    assert res_found.found is True
    assert res_found.check_failed is False
    assert res_found.amount == 1000.0

    res_not_found = ExistenceResult(found=False, invoice_number="INV-2", check_failed=False)
    assert res_not_found.found is False
    assert res_not_found.check_failed is False

    res_failed = ExistenceResult(found=False, invoice_number="INV-3", check_failed=True, error="Network error")
    assert res_failed.found is False
    assert res_failed.check_failed is True
    assert res_failed.error == "Network error"

