"""SQLAlchemy models for Ledger Lens.

Mirrors the schema in the Portfolio Dossier (Ledger Lens §2 "Database & graph
schema") with one addition: `FilingSection` holds the normalized
Document -> Section output of M1 ingestion, ahead of chunking/embedding
(`DocChunk`, M3). The dossier's SQL didn't spell out an intermediate table —
this is the kind of small, deliberate deviation CLAUDE.md asks to be recorded:
we need somewhere to persist normalized text before a chunking strategy
exists to turn it into DocChunk rows.
"""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class IngestionStatus(str, enum.Enum):
    PENDING = "pending"
    OCR_FALLBACK = "ocr_fallback"
    SCHEMA_DRIFT_FLAGGED = "schema_drift_flagged"
    INDEXED = "indexed"
    FAILED = "failed"


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = (
        UniqueConstraint("accession_number", name="uq_filings_accession_number"),
        CheckConstraint(
            "ingestion_status in ('pending','ocr_fallback','schema_drift_flagged','indexed','failed')",
            name="ck_filings_ingestion_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_cik: Mapped[str] = mapped_column(String(10), index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    form_type: Mapped[str] = mapped_column(String(16))
    fiscal_year: Mapped[int | None] = mapped_column(nullable=True)
    accession_number: Mapped[str] = mapped_column(String(32))
    filing_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    ingestion_status: Mapped[str] = mapped_column(String(32), default=IngestionStatus.PENDING.value)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sections: Mapped[list["FilingSection"]] = relationship(back_populates="filing", cascade="all, delete-orphan")
    chunks: Mapped[list["DocChunk"]] = relationship(back_populates="filing", cascade="all, delete-orphan")


class FilingSection(Base):
    """M1 output: one row per normalized section of a filing (e.g. 'Item 1A. Risk Factors')."""

    __tablename__ = "filing_sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("filings.id", ondelete="CASCADE"), index=True)
    section_name: Mapped[str] = mapped_column(String(255))
    section_index: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text)

    filing: Mapped["Filing"] = relationship(back_populates="sections")


class DocChunk(Base):
    """M3: chunk + embedding, built from FilingSection text once chunking exists."""

    __tablename__ = "doc_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("filings.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(255))
    chunk_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)

    filing: Mapped["Filing"] = relationship(back_populates="chunks")


class GoldenQA(Base):
    """M6 eval harness fixtures."""

    __tablename__ = "golden_qa"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str] = mapped_column(Text)
    requires_graph: Mapped[bool] = mapped_column(default=False)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
