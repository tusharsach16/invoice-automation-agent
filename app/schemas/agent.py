from enum import Enum
from pydantic import BaseModel, field_validator


# Exact set of actions the executor knows how to run.
# The LLM may only produce actions from this set.
ALLOWED_ACTIONS: set[str] = {
    "read_invoices",
    "check_purchase_orders",
    "create_invoices",
    "flag_mismatches",
    "generate_report",
}


def validate_action_sequence(actions: list[str]) -> None:
    """
    Validate that action sequence contains only allowed actions and follows
    deterministic ordering and dependency rules.
    """
    if not actions:
        raise ValueError("Plan must contain at least one action")

    unknown = set(actions) - ALLOWED_ACTIONS
    if unknown:
        raise ValueError(f"Unknown actions not permitted: {sorted(unknown)}")

    if len(actions) != len(set(actions)):
        raise ValueError("Duplicate actions in plan are not permitted")

    indices = {action: i for i, action in enumerate(actions)}

    # Rule: check_purchase_orders requires read_invoices before it
    if "check_purchase_orders" in indices:
        if "read_invoices" not in indices or indices["read_invoices"] > indices["check_purchase_orders"]:
            raise ValueError("'read_invoices' must precede 'check_purchase_orders'")

    # Rule: create_invoices requires check_purchase_orders before it
    if "create_invoices" in indices:
        if "check_purchase_orders" not in indices or indices["check_purchase_orders"] > indices["create_invoices"]:
            raise ValueError("'check_purchase_orders' must precede 'create_invoices'")

    # Rule: flag_mismatches requires check_purchase_orders before it
    if "flag_mismatches" in indices:
        if "check_purchase_orders" not in indices or indices["check_purchase_orders"] > indices["flag_mismatches"]:
            raise ValueError("'check_purchase_orders' must precede 'flag_mismatches'")

    # Rule: generate_report must happen after processing
    if "generate_report" in indices:
        report_idx = indices["generate_report"]
        for other_action, other_idx in indices.items():
            if other_action != "generate_report" and other_idx > report_idx:
                raise ValueError(f"'generate_report' must occur after processing ('{other_action}')")


class AgentGoal(BaseModel):
    goal: str


class FilterConfig(BaseModel):
    min_amount: float | None = None
    max_amount: float | None = None
    vendor_ids: list[int] | None = None
    invoice_numbers: list[str] | None = None


class ExecutionPlan(BaseModel):
    """
    Validated output of the LLM planner.

    The validation here enforces both the allowlist and deterministic ordering/dependencies:
    if the LLM generates an invalid, incomplete, or out-of-order plan, it is rejected.
    """
    filters: FilterConfig
    actions: list[str]

    @field_validator("actions")
    @classmethod
    def actions_must_be_valid_sequence(cls, v: list[str]) -> list[str]:
        validate_action_sequence(v)
        return v


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalRequest(BaseModel):
    source_invoice_id: int
    decision: ApprovalDecision
    note: str | None = None


class InvoiceRunStateOut(BaseModel):
    source_invoice_id: int
    invoice_number: str
    amount: float
    approval_status: str
    processing_status: str
    mismatch_reason: str | None
    error_detail: str | None


class AgentRunOut(BaseModel):
    id: int
    goal: str
    status: str
    started_at: str
    finished_at: str | None
    plan_json: str
    report_json: str | None
    invoice_states: list[InvoiceRunStateOut] = []
