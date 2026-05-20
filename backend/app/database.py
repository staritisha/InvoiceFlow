# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — database.py
#  Production-grade synchronous database infrastructure layer.
#  Provides connection pooling, query observability, audit mixins,
#  soft-delete support, pagination helpers, and health monitoring.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    event,
    text,
)
from sqlalchemy import create_engine as _create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.config import settings

# ── Logger ────────────────────────────────────────────────────────────────────

logger = logging.getLogger("invoiceflow.db")

# ── In-Memory DB Metrics ──────────────────────────────────────────────────────

_db_metrics: dict[str, Any] = {
    "total_queries": 0,
    "failed_queries": 0,
    "slow_queries": 0,
    "total_query_time_ms": 0.0,
    "slow_query_threshold_ms": 500,
    "last_error": None,
}

SLOW_QUERY_THRESHOLD_MS: float = 500.0

# ── Naming Conventions ────────────────────────────────────────────────────────
# Ensures consistent, predictable index/constraint names across migrations.

_naming_convention: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# ── Declarative Base ──────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_naming_convention)


# ── Engine ────────────────────────────────────────────────────────────────────

def _build_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")

    connect_args: dict[str, Any] = {}
    pool_kwargs: dict[str, Any] = {}

    if is_sqlite:
        connect_args["check_same_thread"] = False
        # SQLite does not support QueuePool the same way
    else:
        pool_kwargs = {
            "poolclass": QueuePool,
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout,
            "pool_recycle": settings.db_pool_recycle,
            "pool_pre_ping": True,   # auto-reconnect on stale connections
        }

    engine = _create_engine(
        database_url,
        connect_args=connect_args,
        echo=False,   # we handle logging ourselves via events below
        **pool_kwargs,
    )
    return engine


engine: Engine = _build_engine(settings.database_url)

# ── Read Replica (optional) ───────────────────────────────────────────────────
# Set READ_REPLICA_URL in .env to enable read-replica routing.

_read_replica_url: Optional[str] = getattr(settings, "read_replica_url", None)
read_engine: Engine = _build_engine(_read_replica_url) if _read_replica_url else engine

# ── Session Factories ─────────────────────────────────────────────────────────

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

ReadSessionLocal = sessionmaker(
    bind=read_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# ── Query Timing & Observability ──────────────────────────────────────────────

@event.listens_for(engine, "before_cursor_execute")
def _before_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start_time", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def _after_execute(conn, cursor, statement, parameters, context, executemany):
    total_ms = (time.perf_counter() - conn.info["query_start_time"].pop()) * 1000
    _db_metrics["total_queries"] += 1
    _db_metrics["total_query_time_ms"] += total_ms

    query_type = statement.strip().split()[0].upper() if statement.strip() else "UNKNOWN"

    if total_ms >= SLOW_QUERY_THRESHOLD_MS:
        _db_metrics["slow_queries"] += 1
        logger.warning(
            f"[DB SLOW] {query_type} completed in {total_ms:.1f}ms "
            f"(threshold={SLOW_QUERY_THRESHOLD_MS}ms)"
        )
    elif settings.enable_request_logging:
        logger.debug(f"[DB] {query_type} completed in {total_ms:.1f}ms")


@event.listens_for(engine, "handle_error")
def _on_db_error(exception_context):
    _db_metrics["failed_queries"] += 1
    _db_metrics["last_error"] = str(exception_context.original_exception)
    logger.error(f"[DB ERROR] {exception_context.original_exception}")


# ── FastAPI Dependency ────────────────────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """Yield a write database session. Use as a FastAPI Depends()."""
    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"[DB] Session rollback due to: {exc}")
        raise
    finally:
        db.close()


def get_read_db() -> Generator[Session, None, None]:
    """Yield a read-replica session (falls back to primary if no replica configured)."""
    db = ReadSessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def transaction(db: Session):
    """Context manager for explicit transaction control with auto-rollback."""
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


# ── Health & Metrics ──────────────────────────────────────────────────────────

def check_database_health() -> dict[str, Any]:
    """Run a lightweight liveness probe. Returns a status dict."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        pool = engine.pool
        return {
            "status": "connected",
            "pool_size": getattr(pool, "size", lambda: None)(),
            "checked_in": getattr(pool, "checkedin", lambda: None)(),
            "checked_out": getattr(pool, "checkedout", lambda: None)(),
            "overflow": getattr(pool, "overflow", lambda: None)(),
        }
    except OperationalError as exc:
        logger.error(f"[DB] Health check failed: {exc}")
        return {"status": "unreachable", "error": str(exc)}


def get_database_metrics() -> dict[str, Any]:
    """Return accumulated query performance metrics."""
    total = _db_metrics["total_queries"]
    avg_ms = (
        round(_db_metrics["total_query_time_ms"] / total, 2) if total else 0.0
    )
    return {
        "total_queries": total,
        "failed_queries": _db_metrics["failed_queries"],
        "slow_queries": _db_metrics["slow_queries"],
        "average_query_time_ms": avg_ms,
        "total_query_time_ms": round(_db_metrics["total_query_time_ms"], 2),
        "last_error": _db_metrics["last_error"],
    }


def verify_database_connection() -> bool:
    """Called at startup. Returns True if DB is reachable."""
    result = check_database_health()
    if result["status"] == "connected":
        logger.info("[DB] ✓ Database connection verified successfully")
        return True
    logger.error(f"[DB] ✗ Database unreachable: {result.get('error')}")
    return False


def dispose_engine() -> None:
    """Called at shutdown to cleanly close all pool connections."""
    engine.dispose()
    if read_engine is not engine:
        read_engine.dispose()
    logger.info("[DB] ✓ Connection pool disposed")


# ── Mixins ────────────────────────────────────────────────────────────────────

class TimestampMixin:
    """Adds created_at and updated_at columns to any model."""

    created_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class SoftDeleteMixin:
    """
    Adds soft-delete support. Instead of removing rows, set is_deleted=True.
    Filter active records with: query.filter(Model.is_deleted == False)
    """

    is_deleted: bool = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)

    def soft_delete(self, db: Session) -> None:
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)
        db.add(self)
        db.commit()


class AuditMixin(TimestampMixin, SoftDeleteMixin):
    """Combines timestamps and soft-delete into a single mixin for full audit support."""
    pass


# ── Pagination Helper ─────────────────────────────────────────────────────────

def paginate(
    query,
    page: int = 1,
    page_size: int = 20,
    max_page_size: int = 100,
) -> dict[str, Any]:
    """
    Apply limit/offset pagination to any SQLAlchemy query.

    Usage:
        result = paginate(db.query(Invoice), page=2, page_size=25)
        items = result["items"]
    """
    page = max(1, page)
    page_size = min(max(1, page_size), max_page_size)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    total_pages = (total + page_size - 1) // page_size

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
    }


# ── Retry Helper ──────────────────────────────────────────────────────────────

def with_db_retry(fn, retries: int = 3, delay: float = 0.5):
    """
    Execute a callable that uses a DB session, retrying on OperationalError.
    Useful for transient connection failures.
    """
    import time as _time

    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except OperationalError as exc:
            last_exc = exc
            logger.warning(f"[DB] Attempt {attempt}/{retries} failed: {exc}. Retrying in {delay}s…")
            _time.sleep(delay)
    logger.error(f"[DB] All {retries} retry attempts exhausted.")
    raise last_exc


# ── Alembic target (used by env.py) ──────────────────────────────────────────

target_metadata = Base.metadata
