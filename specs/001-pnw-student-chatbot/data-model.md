# Data Model

All timestamps are UTC. Student question text and identity are request/session scoped and never persisted.

| Entity | Key fields and validation | Relationships / rules |
|---|---|---|
| `ApprovedSource` | UUID, unique HTTPS URL, title, owner office, subject, approval=`draft|approved|rejected`, lifecycle=`active|superseded|retired`, timestamps | A source is eligible only when approved+active and has an eligible active revision. Retirement invalidates retrieval/cache and must take effect within one hour. |
| `SourceRevision` | UUID, source FK, immutable SHA-256 content hash, retrieved/effective timestamps, campus (`hammond|westville|all`), optional program/course/term scope, status | One source has many revisions; conflicting active evidence becomes `unresolved` and is excluded until Dean of Students resolution. |
| `SourceChunk` | UUID, revision FK, ordinal, text/heading/table context, citation anchor, `tsvector`, pgvector embedding, embedding model/version/dimension | Immutable retrieval unit. HNSW cosine index when measured need exists; B-tree indexes lifecycle/scope fields. |
| `SourceReviewEvent` | UUID, source/revision FK, reviewer OIDC subject, action (`approve|activate|supersede|retire|resolve_conflict`), reason, timestamp | Append-only audit. Owner office controls subject source; Dean of Students resolves conflicts. |
| `ReferralDirectoryEntry` | UUID, topic, office, URL and/or phone/email, campus scope, active flag | Used for unsupported/account-specific/unresolved responses. |

Request DTO `StudentQuestion` includes optional campus/program/course/term. Response DTO `SupportedAnswer` includes answer, citations, and applied context. Only aggregate non-identifying outcome/latency/source counters are retained.

```text
Source: draft → approved/active → superseded | retired
Revision: pending_review → approved/active → superseded | retired
Chat: emergency | needs_context | referral | unresolved | answer
```
