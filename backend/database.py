"""
database.py - connects Python to the database.

Three important ideas for beginners:
  * engine         -> the actual connection to the database file/server
  * SessionLocal   -> a factory that makes "sessions". A session is like a
                      shopping basket: you add/change objects, then `commit()`
                      to save them all together (or roll back if something fails).
  * Base           -> the parent class of all our table classes (see models.py)
"""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.config import settings

# SQLite needs this flag because FastAPI uses several threads.
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """All database tables inherit from this."""


def utcnow() -> datetime:
    """Current time in UTC (without timezone object, so SQLite compares it easily)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_db():
    """
    FastAPI "dependency": gives each web request its own database session
    and always closes it afterwards (even if an error happens).
    Used in routers like:  db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
