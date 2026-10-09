"""Database tables (SQLAlchemy). Queries live in queries.py."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, DateTime, ForeignKey, Text, UniqueConstraint, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # SQLite stores naive UTC


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(default="running")
    notes: Mapped[str | None] = mapped_column(Text)


class ListingRow(Base):
    __tablename__ = "listings"
    # Dedup rule: one row per (site, job_id).
    __table_args__ = (UniqueConstraint("site", "job_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site: Mapped[str]
    job_id: Mapped[str]
    url: Mapped[str]
    title: Mapped[str | None]
    posting_text: Mapped[str] = mapped_column(Text)
    raw_html: Mapped[str | None] = mapped_column(Text)
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    text_hash: Mapped[str]
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))  # run that first saw it
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DecisionRow(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    matcher: Mapped[str]
    matched: Mapped[bool | None]  # None = unscored
    score: Mapped[float | None]
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def make_engine(url: str = "sqlite:///data/sonar.db") -> Engine:
    """Create the engine and any missing tables."""
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return engine
