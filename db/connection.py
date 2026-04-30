"""SQLAlchemy engine and connection helpers."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

import config

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return a singleton SQLAlchemy engine."""
    global _engine
    if _engine is None:
        connect_args = {}
        if config.DB_STATEMENT_TIMEOUT_MS > 0:
            connect_args["options"] = f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}"

        _engine = create_engine(
            config.DATABASE_URL,
            pool_size=10,
            max_overflow=20,
            pool_timeout=10,
            pool_pre_ping=True,
            pool_recycle=300,
            connect_args=connect_args,
        )
    return _engine


def get_connection():
    """Return a new database connection (context-manager)."""
    return get_engine().connect()
