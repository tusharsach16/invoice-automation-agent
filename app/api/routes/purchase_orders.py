import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from app.db.database import get_db
from app.services import po_service

router = APIRouter()


def _db():
    with get_db() as conn:
        yield conn


@router.get("/purchase-orders")
def list_purchase_orders(conn: sqlite3.Connection = Depends(_db)):
    return po_service.get_all(conn)


@router.get("/purchase-orders/{po_number}")
def get_purchase_order(po_number: str, conn: sqlite3.Connection = Depends(_db)):
    po = po_service.get_by_po_number(conn, po_number)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return po
