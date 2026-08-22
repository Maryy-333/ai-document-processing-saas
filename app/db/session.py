"""
Database engine and session foundation.

Phase 2 scope: connection configuration only. No ORM models are defined
here (Phase 3) and no migrations exist yet (Phase 3). This module exists so
the application can validate its DATABASE_URL configuration at startup and so
later phases have a single place to import the engine/session from.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
