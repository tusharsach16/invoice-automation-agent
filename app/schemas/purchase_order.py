from enum import Enum
from pydantic import BaseModel


class POMatchStatus(str, Enum):
    MATCH = "match"
    AMOUNT_MISMATCH = "amount_mismatch"
    NOT_FOUND = "not_found"


class PurchaseOrderOut(BaseModel):
    id: int
    po_number: str
    vendor_id: int
    vendor_name: str
    amount: float
    description: str | None
    created_at: str

    model_config = {"from_attributes": True}


class POMatchResult(BaseModel):
    source_invoice_id: int
    po_number: str | None
    status: POMatchStatus
    po_amount: float | None = None
    invoice_amount: float = 0.0
    # Human-readable reason populated for non-MATCH statuses.
    reason: str | None = None
