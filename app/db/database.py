"""
SQLite connection factory.

We use the standard library sqlite3 — no ORM required for this scope.
WAL mode is enabled so readers don't block writers during agent runs.
"""
import sqlite3
from contextlib import contextmanager
from typing import Generator

from app.core.config import settings


def get_connection() -> sqlite3.Connection:
    """
    Open a connection to the configured SQLite database.

    Callers are responsible for closing the connection.
    For request-scoped use, prefer get_db() context manager below.
    """
    conn = sqlite3.connect(settings.database_url, check_same_thread=False)
    conn.row_factory = sqlite3.Row       # rows accessible as dicts
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for a single request/operation."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
