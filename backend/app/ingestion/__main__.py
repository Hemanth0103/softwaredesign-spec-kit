"""Synchronous, review-preserving ingestion command; reuse the API environment."""

import argparse
import hashlib
import sys
from importlib import import_module
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy.orm import Session

from app.ai import AIAdapter, DeterministicProvider, Provider
from app.auth import Reviewer
from app.config import Settings, load_settings
from app.db.session import create_db_engine, create_session_factory
from app.ingestion.refresh import rebuild_revision
from app.ingestion.sources import (
    Manifest,
    SourceCollectionError,
    _read_file,
    collect_manifest,
    load_manifest,
    validate_official_url,
)
from app.ingestion.store import ingest_revision
from app.models.referral import ReferralDirectoryEntry
from app.models.source import ApprovedSource, SourceRevision
from app.services.source_governance import review_source


class FixtureContact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: UUID
    topic: str = Field(min_length=1)
    office: str = Field(min_length=1)
    contact_url: str
    campus: Literal["hammond", "westville", "all"] = "all"
    phone: str | None = None
    email: str | None = None


def seed_fixtures(session: Session, manifest: Manifest, path: Path) -> int:
    """Only create absent identities; never approve/restore existing database rows."""
    created = 0
    for entry in manifest.documents:
        if entry.path is None:
            raise SourceCollectionError("Fixture seeding requires local snapshots.")
        snapshot = (path.parent / entry.path).resolve()
        if Path(entry.path).is_absolute() or not snapshot.is_relative_to(path.parent):
            raise SourceCollectionError("Fixture snapshot must stay inside manifest directory.")
        content = _read_file(snapshot, 10 * 1024 * 1024)
        if hashlib.sha256(content).hexdigest() != entry.sha256:
            raise SourceCollectionError(
                "Fixture hash mismatch; correct the manifest before seeding."
            )
        reviewer = Reviewer("fixture:local-test-only", frozenset({entry.owner_office}))
        reason = "Synthetic local/test approval only; no university approval claimed."
        source = session.get(ApprovedSource, entry.source_id)
        if source is None:
            source = ApprovedSource(
                id=entry.source_id,
                canonical_url=entry.canonical_url,
                title="Local/test fixture",
                owner_office=entry.owner_office,
                subject_area="Local/test fixture",
            )
            session.add(source)
            session.flush()
            review_source(
                session, reviewer=reviewer, source_id=source.id, action="approve", reason=reason
            )
        elif (
            source.canonical_url != entry.canonical_url
            or source.owner_office != entry.owner_office
            or source.approval_status != "approved"
            or source.lifecycle_status != "active"
        ):
            raise SourceCollectionError(
                "Existing source is incompatible; fixture seeding cannot change it."
            )
        revision = session.get(SourceRevision, entry.revision_id)
        if revision is None:
            revision = SourceRevision(
                id=entry.revision_id,
                source_id=source.id,
                content_hash=entry.sha256,
                retrieved_at=entry.retrieved_at,
                campus="all",
                readable=True,
            )
            session.add(revision)
            session.flush()
            review_source(
                session,
                reviewer=reviewer,
                source_id=source.id,
                revision_id=revision.id,
                action="approve",
                reason=reason,
            )
            created += 1
        elif revision.source_id != source.id or revision.content_hash != entry.sha256:
            raise SourceCollectionError("Existing fixture revision does not match the manifest.")
    return created


def seed_referrals(session: Session, path: Path) -> int:
    contacts = TypeAdapter(list[FixtureContact]).validate_json(_read_file(path, 1024 * 1024))
    if len(contacts) > 1000 or len({contact.id for contact in contacts}) != len(contacts):
        raise ValueError("Invalid referral fixture count or duplicate IDs.")
    created = 0
    for contact in contacts:
        validate_official_url(contact.contact_url)
        values = contact.model_dump()
        existing = session.get(ReferralDirectoryEntry, contact.id)
        if existing is None:
            session.add(ReferralDirectoryEntry(**values))
            session.flush()
            created += 1
        elif any(getattr(existing, key) != value for key, value in values.items()):
            raise ValueError("Existing referral differs; fixture seeding cannot overwrite it.")
    return created


def provider(settings: Settings, factory_name: str | None, deterministic: bool) -> AIAdapter:
    if deterministic:
        selected: Provider = DeterministicProvider()
    else:
        if not factory_name or ":" not in factory_name:
            raise ValueError(
                "Supply --provider-factory module:callable for the configured AI provider."
            )
        module, name = factory_name.split(":", 1)
        selected = cast(Provider, getattr(import_module(module), name)(settings))
    return AIAdapter(settings, providers={settings.ai_provider: selected})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Prepare configured embedding identity without activation",
    )
    parser.add_argument(
        "--environment", choices=("local", "test", "production"), default="production"
    )
    parser.add_argument(
        "--seed-fixtures",
        action="store_true",
        help="Create absent local/test snapshot identities and synthetic approval audits",
    )
    parser.add_argument("--seed-referrals", type=Path, help="Explicit local/test referral JSON")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--provider-factory",
        help="Deployment module:callable accepting Settings and returning Provider",
    )
    group.add_argument(
        "--deterministic",
        action="store_true",
        help="Explicit offline local/test embedding substitute",
    )
    args = parser.parse_args(argv)
    if (
        args.seed_fixtures or args.seed_referrals or args.deterministic
    ) and args.environment == "production":
        parser.error(
            "Fixture seeding and deterministic embeddings require --environment local or test "
            "and a dedicated database."
        )
    engine = None
    prepared = chunks = pending = failed = seeded = referrals = 0
    try:
        settings = load_settings()
        adapter = provider(settings, args.provider_factory, args.deterministic)
        manifest_path = args.manifest.resolve()
        manifest = load_manifest(manifest_path)
        engine = create_db_engine(
            settings.database_url.get_secret_value(), settings.database_timeout_seconds
        )
        factory = create_session_factory(engine)
        with factory.begin() as session:
            if args.seed_fixtures:
                seeded = seed_fixtures(session, manifest, manifest_path)
            if args.seed_referrals:
                referrals = seed_referrals(session, args.seed_referrals)
        with factory.begin() as session:
            collected = collect_manifest(
                session, manifest_path, timeout_seconds=settings.source_timeout_seconds
            )
        operation = rebuild_revision if args.rebuild else ingest_revision
        for document in collected:
            if document.review_required:
                pending += 1
                print(
                    f"revision={document.revision_id}: owning-office review required; "
                    "update the manifest after approval.",
                    file=sys.stderr,
                )
                continue
            try:
                assert document.content is not None
                identifiers = operation(
                    factory,
                    revision_id=document.revision_id,
                    content=document.content,
                    media_type=document.media_type,
                    embed=adapter.embed,
                    model=settings.ai_embedding_model,
                    version=settings.embedding_version,
                    dimension=settings.embedding_dimension,
                    chunk_size=settings.chunk_size,
                    chunk_overlap=settings.chunk_overlap,
                    timeout_seconds=settings.ai_timeout_seconds,
                )
                prepared += 1
                chunks += len(identifiers)
            except Exception:
                failed += 1
                print(
                    f"revision={document.revision_id}: preparation failed; "
                    "check approval, eligibility, "
                    "extraction, embedding identity and provider availability; retry safely.",
                    file=sys.stderr,
                )
    except Exception:
        failed += 1
        print(
            "Ingestion failed: check environment settings, provider factory, manifest "
            "schema/snapshot hashes, owning-office approval audits and migrated database "
            "availability. No approval is granted by normal imports.",
            file=sys.stderr,
        )
    finally:
        if engine is not None:
            engine.dispose()
    print(
        f"prepared={prepared} chunks={chunks} review_required={pending} failed={failed} "
        f"seeded={seeded} referrals_seeded={referrals}"
    )
    return 1 if failed or pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
