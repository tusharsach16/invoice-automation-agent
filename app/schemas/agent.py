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


class AgentGoal(BaseModel):
    goal: str


class FilterConfig(BaseModel):
    min_amount: float | None = None
    max_amount: float | None = None
    vendor_ids: list[int] | None = None


class ExecutionPlan(BaseModel):
    """
    Validated output of the LLM planner.

    The allowlist check here is the security boundary: if the LLM hallucinates
    a non-existent action, execution is rejected before anything runs.
    """
    filters: FilterConfig
    actions: list[str]

    @field_validator("actions")
    @classmethod
    def actions_must_be_allowlisted(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Plan must contain at least one action")
        unknown = set(v) - ALLOWED_ACTIONS
        if unknown:
            raise ValueError(f"Unknown actions not permitted: {sorted(unknown)}")
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
