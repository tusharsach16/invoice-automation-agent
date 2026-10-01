"""
Application factory.

Responsibilities: configure logging, initialise DB, mount routers and static
files, detect orphaned runs from a prior crash.

No business logic lives here.
"""
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.database import get_connection
from app.db.models import init_db
from app.db.seed import seed_all
from app.api.routes import invoices, purchase_orders, agent, pages

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title="Hulchul ERP", version="1.0.0")

# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(invoices.router)
app.include_router(purchase_orders.router)
app.include_router(agent.router)
app.include_router(pages.router)   # HTML pages last (catch-all friendly)


@app.on_event("startup")
def startup() -> None:
    conn = get_connection()
    try:
        init_db(conn)
        seed_all(conn)

        # Any run still marked 'running' when the server starts is an orphan
        # from a previous process crash. Mark them failed so the state is honest.
        orphans = conn.execute(
            "SELECT id FROM agent_runs WHERE status = 'running'"
        ).fetchall()
        if orphans:
            ids = [r["id"] for r in orphans]
            conn.execute(
                f"UPDATE agent_runs SET status='failed', report_json='{{\"error\":\"process_crash\"}}' "
                f"WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.commit()
            logger.warning("Marked %d orphaned run(s) as failed: %s", len(ids), ids)

        logger.info("Startup complete. DB initialised and seeded.")
    finally:
        conn.close()