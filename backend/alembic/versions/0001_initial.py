"""Initial governed corpus and referral schema.

Revision ID: 0001_initial
Revises: None
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "approved_source",
        sa.Column("canonical_url", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("owner_office", sa.String(), nullable=False),
        sa.Column("subject_area", sa.String(), nullable=False),
        sa.Column("approval_status", sa.String(), server_default="draft", nullable=False),
        sa.Column("lifecycle_status", sa.String(), server_default="active", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "approval_status IN ('draft', 'approved', 'rejected')",
            name=op.f("ck_approved_source_approval_status"),
        ),
        sa.CheckConstraint(
            "canonical_url LIKE 'https://%'", name=op.f("ck_approved_source_https_url")
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('active', 'superseded', 'retired')",
            name=op.f("ck_approved_source_lifecycle_status"),
        ),
        sa.CheckConstraint(
            "length(trim(owner_office)) > 0", name=op.f("ck_approved_source_owner_nonempty")
        ),
        sa.CheckConstraint(
            "length(trim(subject_area)) > 0", name=op.f("ck_approved_source_subject_nonempty")
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0", name=op.f("ck_approved_source_title_nonempty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approved_source")),
        sa.UniqueConstraint("canonical_url", name=op.f("uq_approved_source_canonical_url")),
    )
    op.create_index(
        "ix_source_eligibility",
        "approved_source",
        ["approval_status", "lifecycle_status"],
        unique=False,
    )
    op.create_table(
        "referral_directory_entry",
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("office", sa.String(), nullable=False),
        sa.Column("contact_url", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("campus", sa.String(), server_default="all", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "campus IN ('hammond', 'westville', 'all')",
            name=op.f("ck_referral_directory_entry_campus"),
        ),
        sa.CheckConstraint(
            "contact_url IS NULL OR contact_url LIKE 'https://%'",
            name=op.f("ck_referral_directory_entry_https_contact"),
        ),
        sa.CheckConstraint(
            "coalesce(length(trim(contact_url)), 0) > 0 OR "
            "coalesce(length(trim(phone)), 0) > 0 OR "
            "coalesce(length(trim(email)), 0) > 0",
            name=op.f("ck_referral_directory_entry_contact_required"),
        ),
        sa.CheckConstraint(
            "length(trim(office)) > 0", name=op.f("ck_referral_directory_entry_office_nonempty")
        ),
        sa.CheckConstraint(
            "length(trim(topic)) > 0", name=op.f("ck_referral_directory_entry_topic_nonempty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_referral_directory_entry")),
    )
    op.create_index(
        "ix_referral_topic_active_campus",
        "referral_directory_entry",
        ["topic", "active", "campus"],
        unique=False,
    )
    op.create_table(
        "source_revision",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("campus", sa.String(), server_default="all", nullable=False),
        sa.Column("program", sa.String(length=160), nullable=True),
        sa.Column("course", sa.String(length=32), nullable=True),
        sa.Column("academic_term", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(), server_default="pending_review", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "campus IN ('hammond', 'westville', 'all')", name=op.f("ck_source_revision_campus")
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'approved', 'active', 'superseded', 'retired')",
            name=op.f("ck_source_revision_status"),
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name=op.f("ck_source_revision_effective_window"),
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name=op.f("ck_source_revision_hash_length")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["approved_source.id"],
            name=op.f("fk_source_revision_source_id_approved_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_revision")),
        sa.UniqueConstraint("source_id", "content_hash", name="uq_revision_source_hash"),
    )
    op.create_index(
        "ix_revision_effective",
        "source_revision",
        ["effective_from", "effective_until"],
        unique=False,
    )
    op.create_index(
        "ix_revision_scope",
        "source_revision",
        ["campus", "program", "course", "academic_term"],
        unique=False,
    )
    op.create_index(
        "ix_revision_source_status", "source_revision", ["source_id", "status"], unique=False
    )
    op.create_table(
        "source_chunk",
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("table_context", sa.Text(), nullable=True),
        sa.Column("citation_anchor", sa.Text(), nullable=True),
        sa.Column("search_vector", postgresql.TSVECTOR(), server_default="", nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column("embedding_version", sa.String(), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "embedding_dimension > 0", name=op.f("ck_source_chunk_dimension_positive")
        ),
        sa.CheckConstraint(
            "length(trim(embedding_model)) > 0", name=op.f("ck_source_chunk_model_nonempty")
        ),
        sa.CheckConstraint(
            "length(trim(embedding_version)) > 0", name=op.f("ck_source_chunk_version_nonempty")
        ),
        sa.CheckConstraint("length(trim(text)) > 0", name=op.f("ck_source_chunk_text_nonempty")),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_source_chunk_ordinal_nonnegative")),
        sa.CheckConstraint(
            "vector_dims(embedding) = embedding_dimension",
            name=op.f("ck_source_chunk_vector_dimension"),
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["source_revision.id"],
            name=op.f("fk_source_chunk_revision_id_source_revision"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_chunk")),
        sa.UniqueConstraint(
            "revision_id",
            "ordinal",
            "embedding_model",
            "embedding_version",
            name="uq_chunk_revision_ordinal_model_version",
        ),
    )
    op.create_index(
        "ix_chunk_embedding_identity",
        "source_chunk",
        ["embedding_model", "embedding_version", "embedding_dimension"],
        unique=False,
    )
    op.create_index(
        "ix_chunk_search_vector",
        "source_chunk",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "source_review_event",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("reviewer_subject", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "action IN ('approve', 'activate', 'supersede', 'retire', 'resolve_conflict')",
            name=op.f("ck_source_review_event_action"),
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0", name=op.f("ck_source_review_event_reason_nonempty")
        ),
        sa.CheckConstraint(
            "length(trim(reviewer_subject)) > 0",
            name=op.f("ck_source_review_event_reviewer_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["source_revision.id"],
            name=op.f("fk_source_review_event_revision_id_source_revision"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["approved_source.id"],
            name=op.f("fk_source_review_event_source_id_approved_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_review_event")),
    )
    op.create_index("ix_review_revision", "source_review_event", ["revision_id"], unique=False)
    op.create_index(
        "ix_review_source_timestamp",
        "source_review_event",
        ["source_id", "timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("source_review_event")
    op.drop_table("source_chunk")
    op.drop_table("source_revision")
    op.drop_table("referral_directory_entry")
    op.drop_table("approved_source")
    # Keep the shared vector extension: other applications may use it.
