"""Database engine and session helpers.

DATABASE_URL defaults to a local SQLite file so nothing needs installing.
Tests override `get_session` with an in-memory database (see tests/conftest.py).

"""
import os
from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

# On Vercel the project directory is read-only; /tmp is the only writable path
# (and is wiped on cold start).
_default_url = "sqlite:////tmp/policydesk.db" if os.getenv("VERCEL") else "sqlite:///./policydesk.db"

DATABASE_URL = os.getenv("DATABASE_URL", _default_url)

# check_same_thread=False lets FastAPI use the connection across threads.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session