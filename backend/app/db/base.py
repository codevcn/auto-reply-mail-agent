"""SQLAlchemy 2 Declarative Base with AsyncAttrs."""

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all database models with async attribute support."""

    pass
