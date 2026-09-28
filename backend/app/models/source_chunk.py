"""Immutable retrieval units. Rebuilds insert new chunks rather than editing history."""

import math
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Connection,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.db.base import Base, UUIDMixin
from app.models.source import SourceRevision


class SourceChunk(UUIDMixin, Base):
    __tablename__ = "source_chunk"
    __table_args__ = (
        UniqueConstraint(
            "revision_id",
            "ordinal",
            "embedding_model",
            "embedding_version",
            name="uq_chunk_revision_ordinal_model_version",
        ),
        Index("ix_chunk_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_chunk_embedding_identity",
            "embedding_model",
            "embedding_version",
            "embedding_dimension",
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        CheckConstraint("length(trim(text)) > 0", name="text_nonempty"),
        CheckConstraint("embedding_dimension > 0", name="dimension_positive"),
        CheckConstraint("vector_dims(embedding) = embedding_dimension", name="vector_dimension"),
        CheckConstraint("length(trim(embedding_model)) > 0", name="model_nonempty"),
        CheckConstraint("length(trim(embedding_version)) > 0", name="version_nonempty"),
    )

    revision_id: Mapped[UUID] = mapped_column(ForeignKey("source_revision.id", ondelete="RESTRICT"))
    revision: Mapped[SourceRevision] = relationship()
    ordinal: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    heading: Mapped[str | None] = mapped_column(Text)
    table_context: Mapped[str | None] = mapped_column(Text)
    citation_anchor: Mapped[str | None] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(TSVECTOR, server_default="")
    # Dimension is stored per row, allowing future model rebuilds without schema changes.
    embedding: Mapped[list[float]] = mapped_column(Vector())
    embedding_model: Mapped[str] = mapped_column(String)
    embedding_version: Mapped[str] = mapped_column(String)
    embedding_dimension: Mapped[int] = mapped_column()


@event.listens_for(SourceChunk, "before_insert")
def validate_chunk(
    mapper: Mapper[SourceChunk], connection: Connection, target: SourceChunk
) -> None:
    if target.ordinal < 0 or not target.text.strip():
        raise ValueError("Chunk requires a nonnegative ordinal and non-empty text")
    if not target.embedding_model.strip() or not target.embedding_version.strip():
        raise ValueError("Chunk requires embedding model and version")
    if (
        target.embedding_dimension <= 0
        or len(target.embedding) != target.embedding_dimension
        or not all(math.isfinite(value) for value in target.embedding)
    ):
        raise ValueError("Embedding must be finite and match its declared dimension")


@event.listens_for(SourceChunk, "before_update")
@event.listens_for(SourceChunk, "before_delete")
def reject_chunk_change(
    mapper: Mapper[SourceChunk], connection: Connection, target: SourceChunk
) -> None:
    raise ValueError("Source chunks are immutable; insert a new retrieval unit")
