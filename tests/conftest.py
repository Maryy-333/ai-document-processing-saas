"""
Shared test fixtures for model/DB tests.

Uses a real PostgreSQL database (not SQLite) — the schema relies on
Postgres-specific features (native ENUM types, UUID columns) and on foreign
key / uniqueness enforcement that SQLite does not reliably reproduce, so a
substitute engine would not actually validate the constraints this phase
cares about.

Test DB URL is intentionally separate from the app's DATABASE_URL so tests
never run against the dev database. Migrations must be applied to the test
DB once beforehand (see README / "commands to run tests").

Each test runs inside an outer transaction that is rolled back afterward, so
tests are isolated from each other without recreating the schema every time.
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:733586@localhost:5432/invoice_extractor_test",
)

_engine = create_engine(TEST_DATABASE_URL, future=True)
_SessionFactory = sessionmaker(bind=_engine, future=True)


@pytest.fixture()
def db_session() -> Session:
    connection = _engine.connect()
    transaction = connection.begin()
    session = _SessionFactory(bind=connection)

    try:
        yield session
    finally:
        session.close()
        # A test that triggers an IntegrityError (etc.) already aborts the
        # underlying DB transaction; rolling back an already-inactive
        # transaction is a no-op we should skip rather than warn about.
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture()
def local_storage(tmp_path):
    """
    Isolated LocalStorageService rooted in a pytest tmp_path — never touches
    the real configured storage_dir, and is torn down automatically by
    pytest after the test.
    """
    from app.storage.local import LocalStorageService

    return LocalStorageService(str(tmp_path / "uploads"))

