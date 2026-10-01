"""
Server-rendered HTML pages using Jinja2.
All data fetching happens here; templates receive plain dicts.
"""
import json
import sqlite3
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.database import get_db
from app.services import (
    source_invoice_service,
    erp_invoice_service,
    po_service,
)

router = APIRouter()
from jinja2 import Environment, FileSystemLoader

_jinja_env = Environment(
    loader=FileSystemLoader("app/templates"),
    cache_size=0,
    auto_reload=True,
)
templates = Jinja2Templates(env=_jinja_env)


def _db():
    with get_db() as conn:
        yield conn


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn: sqlite3.Connection = Depends(_db)):
    source = source_invoice_service.get_all(conn)
    erp = erp_invoice_service.get_all(conn)
    pos = po_service.get_all(conn)
    runs = conn.execute(
        "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 5"
    ).fetchall()
    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "source_count": len(source),
        "erp_count": len(erp),
        "po_count": len(pos),
        "recent_runs": [dict(r) for r in runs],
    })


@router.get("/source-invoices", response_class=HTMLResponse)
def source_invoices_page(request: Request, conn: sqlite3.Connection = Depends(_db)):
    invoices = source_invoice_service.get_all(conn)
    return templates.TemplateResponse(request=request, name="source_invoices.html", context={
        "invoices": invoices,
    })


@router.get("/erp-invoices", response_class=HTMLResponse)
def erp_invoices_page(request: Request, conn: sqlite3.Connection = Depends(_db)):
    invoices = erp_invoice_service.get_all(conn)
    return templates.TemplateResponse(request=request, name="erp_invoices.html", context={
        "invoices": invoices,
    })


@router.get("/erp-invoices/{invoice_number}", response_class=HTMLResponse)
def erp_invoice_detail(
    request: Request,
    invoice_number: str,
    conn: sqlite3.Connection = Depends(_db),
):
    inv = erp_invoice_service.get_by_invoice_number(conn, invoice_number)
    not_found = inv is None
    return templates.TemplateResponse(request=request, name="erp_invoice_detail.html", context={
        "invoice": inv,
        "not_found": not_found,
        "invoice_number": invoice_number,
    })


@router.get("/create-invoice", response_class=HTMLResponse)
def create_invoice_page(request: Request, conn: sqlite3.Connection = Depends(_db)):
    vendors = conn.execute("SELECT id, name FROM vendors ORDER BY name").fetchall()
    return templates.TemplateResponse(request=request, name="create_invoice.html", context={
        "vendors": [dict(v) for v in vendors],
    })


@router.get("/purchase-orders", response_class=HTMLResponse)
def purchase_orders_page(request: Request, conn: sqlite3.Connection = Depends(_db)):
    pos = po_service.get_all(conn)
    return templates.TemplateResponse(request=request, name="purchase_orders.html", context={
        "pos": pos,
    })


@router.get("/agent-runs", response_class=HTMLResponse)
def agent_runs_page(request: Request, conn: sqlite3.Connection = Depends(_db)):
    runs = conn.execute(
        "SELECT * FROM agent_runs ORDER BY started_at DESC"
    ).fetchall()
    return templates.TemplateResponse(request=request, name="agent_runs.html", context={
        "runs": [dict(r) for r in runs],
    })


@router.get("/agent-runs/{run_id}", response_class=HTMLResponse)
def agent_run_detail(
    request: Request, run_id: int, conn: sqlite3.Connection = Depends(_db),
):
    run = conn.execute(
        "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not run:
        return HTMLResponse("<h1>Run not found</h1>", status_code=404)

    states = conn.execute(
        """
        SELECT irs.source_invoice_id, si.invoice_number, si.amount,
               irs.approval_status, irs.processing_status,
               irs.mismatch_reason, irs.error_detail, irs.updated_at
        FROM invoice_run_state irs
        JOIN source_invoices si ON si.id = irs.source_invoice_id
        WHERE irs.run_id = ?
        ORDER BY si.amount DESC
        """,
        (run_id,),
    ).fetchall()

    actions = conn.execute(
        """
        SELECT aa.action, aa.outcome, aa.detail, aa.created_at,
               si.invoice_number
        FROM agent_actions aa
        LEFT JOIN source_invoices si ON si.id = aa.source_invoice_id
        WHERE aa.run_id = ?
        ORDER BY aa.created_at
        """,
        (run_id,),
    ).fetchall()

    report = None
    if run["report_json"]:
        try:
            report = json.loads(run["report_json"])
        except json.JSONDecodeError:
            pass

    return templates.TemplateResponse(request=request, name="agent_run.html", context={
        "run": dict(run),
        "states": [dict(s) for s in states],
        "actions": [dict(a) for a in actions],
        "report": report,
    })
