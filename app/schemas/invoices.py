from datetime import date
from pydantic import BaseModel, Field


class SourceInvoiceOut(BaseModel):
    id: int
    invoice_number: str
    vendor_id: int
    vendor_name: str
    po_number: str | None
    amount: float
    description: str | None
    invoice_date: str

    model_config = {"from_attributes": True}


class ErpInvoiceIn(BaseModel):
    """Validated input for creating an ERP invoice via the form route."""
    invoice_number: str = Field(min_length=1, max_length=50)
    vendor_id: int = Field(gt=0)
    po_number: str | None = None
    amount: float = Field(gt=0)
    description: str | None = None


class ErpInvoiceOut(BaseModel):
    id: int
    invoice_number: str
    vendor_id: int
    vendor_name: str
    po_number: str | None
    amount: float
    entered_at: str

    model_config = {"from_attributes": True}
